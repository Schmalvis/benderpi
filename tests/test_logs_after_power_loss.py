"""A power loss mid-write leaves NUL bytes in a log, in front of the next record.

Found 2026-11-02 in the device's logs/2026-10-31.jsonl, line 63: 322 NUL bytes,
then a valid session_start that the next boot appended. ext4 had grown the file
but not written its data when the power went at 19:03. str.strip() keeps NUL,
so review_log.py crashed on the line, and every other reader dropped the record.
"""
import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

NUL = "\x00" * 322


def _rec(**kw):
    kw.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return json.dumps(kw)


def _write(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def test_review_log_reads_the_record_behind_the_nul_run(tmp_path, monkeypatch):
    import review_log
    day = datetime.now().strftime("%Y-%m-%d")
    _write(tmp_path / f"{day}.jsonl", [
        _rec(type="turn", user_text="before"),
        NUL + _rec(type="turn", user_text="after"),
    ])
    monkeypatch.setattr(review_log, "LOG_DIR", str(tmp_path))
    assert [t["user_text"] for t in review_log.load_turns(days=7)] == [
        "before", "after"]


def test_review_log_skips_a_line_it_cannot_read(tmp_path, monkeypatch):
    import review_log
    day = datetime.now().strftime("%Y-%m-%d")
    _write(tmp_path / f"{day}.jsonl", [
        '{"type": "turn", "user_te', _rec(type="turn", user_text="ok")])
    monkeypatch.setattr(review_log, "LOG_DIR", str(tmp_path))
    assert [t["user_text"] for t in review_log.load_turns(days=7)] == ["ok"]


def test_watchdog_finds_the_session_start_behind_the_nul_run(tmp_path):
    from watchdog import _find_latest_session_start
    ts = "2026-10-31T19:34:10.031006+00:00"
    _write(tmp_path / "2026-10-31.jsonl", [
        _rec(type="session_start", ts="2026-10-31T18:00:00+00:00"),
        NUL + _rec(type="session_start", ts=ts),
    ])
    assert _find_latest_session_start(str(tmp_path)) == datetime.fromisoformat(ts)


def test_watchdog_metrics_read_the_record_behind_the_nul_run(tmp_path):
    from watchdog import _load_metrics
    live = tmp_path / "metrics.jsonl"
    _write(live, [NUL + _rec(type="counter", name="after_power_loss")])
    names = [e.get("name") for e in _load_metrics(str(live), lookback_hours=24)]
    assert names == ["after_power_loss"]


def _client():
    os.environ["BENDER_WEB_PIN"] = "testpin"
    from fastapi.testclient import TestClient
    from web.app import app
    from web.auth import issue_token
    return TestClient(app), {"X-Bender-Token": issue_token()}


def test_the_logs_page_shows_the_session_behind_the_nul_run(tmp_path):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _write(tmp_path / f"{day}.jsonl", [
        NUL + _rec(type="session_start", session_id="s1",
                   ts="2026-10-31T19:34:10+00:00"),
        _rec(type="session_end", session_id="s1",
             ts="2026-10-31T19:34:44+00:00"),
    ])
    client, hdr = _client()
    with patch("web.routes.logs._LOG_DIR", str(tmp_path)):
        resp = client.get(f"/api/logs/conversations/{day}", headers=hdr)
    assert resp.status_code == 200
    events = resp.json()["entries"]
    assert [e["type"] for e in events] == ["session_start", "session_end"]
    assert events[1]["duration_s"] == 34.0  # needs the start it lost before


def test_the_metrics_page_reads_the_record_behind_the_nul_run(tmp_path):
    live = tmp_path / "metrics.jsonl"
    _write(live, [NUL + _rec(type="counter", name="after_power_loss")])
    client, hdr = _client()
    with patch("web.routes.logs._METRICS_LOG", str(live)):
        resp = client.get("/api/logs/metrics?hours=24", headers=hdr)
    assert resp.status_code == 200
    assert [e.get("name") for e in resp.json()["entries"]] == ["after_power_loss"]


def test_review_log_survives_bad_bytes_and_a_bare_value(tmp_path, monkeypatch):
    import review_log
    day = datetime.now().strftime("%Y-%m-%d")
    with open(tmp_path / f"{day}.jsonl", "wb") as f:
        f.write(b"\xff\xfe torn\n5\n" + _rec(type="turn", user_text="ok").encode() + b"\n")
    monkeypatch.setattr(review_log, "LOG_DIR", str(tmp_path))
    assert [t["user_text"] for t in review_log.load_turns(days=7)] == ["ok"]


def test_frame_outcomes_read_the_row_behind_the_nul_run(tmp_path, monkeypatch):
    import turn_frames
    monkeypatch.setattr(turn_frames, "frames_dir", lambda: str(tmp_path))
    (tmp_path / "2026-10-31").mkdir()
    _write(tmp_path / "2026-10-31" / "outcomes.jsonl",
           [NUL + json.dumps({"outcome": "saved"})])
    assert turn_frames._read_outcomes("2026-10-31") == [{"outcome": "saved"}]
