"""Tests for log viewer API endpoints."""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"


def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    import importlib
    import web.app
    importlib.reload(web.app)
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def auth():
    return {"X-Bender-Pin": PIN}


# ── /api/logs/conversations ──────────────────────────────────────────


def test_conversations_list_empty_dir():
    """Returns empty list when log dir does not exist."""
    client = get_client()
    with patch("web.app._LOG_DIR", "/nonexistent/path"):
        resp = client.get("/api/logs/conversations", headers=auth())
    assert resp.status_code == 200
    assert resp.json() == {"files": []}


def test_conversations_list_returns_jsonl_files():
    """Lists JSONL files from log dir, excluding metrics.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        today = datetime.now(tz=timezone.utc).date()
        fname = today.strftime("%Y-%m-%d") + ".jsonl"
        open(os.path.join(tmpdir, fname), "w").close()
        open(os.path.join(tmpdir, "metrics.jsonl"), "w").close()  # must be excluded
        open(os.path.join(tmpdir, "other.txt"), "w").close()      # not jsonl

        client = get_client()
        with patch("web.app._LOG_DIR", tmpdir):
            resp = client.get("/api/logs/conversations?days=7", headers=auth())

    assert resp.status_code == 200
    data = resp.json()
    filenames = [f["filename"] for f in data["files"]]
    assert fname in filenames
    assert "metrics.jsonl" not in filenames
    assert "other.txt" not in filenames


def test_conversations_list_requires_pin():
    client = get_client()
    resp = client.get("/api/logs/conversations")
    assert resp.status_code == 401


# ── /api/logs/conversations/{date} ──────────────────────────────────


def test_conversations_date_not_found():
    client = get_client()
    with patch("web.app._LOG_DIR", "/nonexistent/path"):
        resp = client.get("/api/logs/conversations/2026-01-01", headers=auth())
    assert resp.status_code == 404


def test_conversations_date_invalid_format():
    client = get_client()
    resp = client.get("/api/logs/conversations/not-a-date", headers=auth())
    assert resp.status_code == 400


def test_conversations_date_parses_events():
    """Parses JSONL and computes session duration."""
    now = datetime.now(tz=timezone.utc)
    start_ts = now.isoformat()
    end_ts = (now + timedelta(seconds=30)).isoformat()
    sid = "abc123"

    lines = [
        json.dumps({"type": "session_start", "session_id": sid, "ts": start_ts}),
        json.dumps({"type": "turn", "session_id": sid, "ts": now.isoformat(),
                    "user_text": "hello", "intent": "GREETING", "method": "real_clip"}),
        json.dumps({"type": "session_end", "session_id": sid, "ts": end_ts, "reason": "dismissal"}),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        date_str = now.strftime("%Y-%m-%d")
        fpath = os.path.join(tmpdir, date_str + ".jsonl")
        with open(fpath, "w") as f:
            f.write("\n".join(lines))

        client = get_client()
        with patch("web.app._LOG_DIR", tmpdir):
            resp = client.get(f"/api/logs/conversations/{date_str}", headers=auth())

    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 3
    # session_end should have duration_s
    end_event = next(e for e in events if e["type"] == "session_end")
    assert "duration_s" in end_event
    assert abs(end_event["duration_s"] - 30.0) < 1.0


def test_conversations_date_skips_invalid_json():
    """Silently skips malformed JSONL lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        date_str = "2026-03-15"
        fpath = os.path.join(tmpdir, date_str + ".jsonl")
        with open(fpath, "w") as f:
            f.write('{"type": "session_start", "session_id": "x", "ts": "2026-03-15T10:00:00Z"}\n')
            f.write("not json at all\n")
            f.write('{"type": "turn", "session_id": "x"}\n')

        client = get_client()
        with patch("web.app._LOG_DIR", tmpdir):
            resp = client.get(f"/api/logs/conversations/{date_str}", headers=auth())

    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 2  # only 2 valid lines


# ── /api/logs/system ────────────────────────────────────────────────


def test_system_log_returns_lines():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write("2026-03-15 INFO first line\n")
        f.write("2026-03-15 ERROR bad thing happened\n")
        fname = f.name

    try:
        client = get_client()
        with patch("web.app._BENDER_LOG", fname):
            resp = client.get("/api/logs/system?lines=50", headers=auth())
        assert resp.status_code == 200
        lines = resp.json()["lines"]
        assert any("INFO" in ln for ln in lines)
        assert any("ERROR" in ln for ln in lines)
    finally:
        os.unlink(fname)


def test_system_log_filters_by_level():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        f.write("INFO first line\n")
        f.write("ERROR bad thing\n")
        fname = f.name

    try:
        client = get_client()
        with patch("web.app._BENDER_LOG", fname):
            resp = client.get("/api/logs/system?lines=50&level=ERROR", headers=auth())
        assert resp.status_code == 200
        lines = resp.json()["lines"]
        assert all("ERROR" in ln for ln in lines)
        assert not any("INFO" in ln for ln in lines)
    finally:
        os.unlink(fname)


def test_system_log_invalid_level():
    client = get_client()
    resp = client.get("/api/logs/system?level=BADLEVEL", headers=auth())
    assert resp.status_code == 400


def test_system_log_missing_file_returns_empty():
    client = get_client()
    with patch("web.app._BENDER_LOG", "/nonexistent/bender.log"):
        resp = client.get("/api/logs/system", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["lines"] == []


# ── /api/logs/metrics ───────────────────────────────────────────────


def test_metrics_returns_events():
    now = datetime.now(tz=timezone.utc)
    events = [
        {"type": "timer", "name": "stt_transcribe", "duration_ms": 350, "ts": now.isoformat()},
        {"type": "counter", "name": "session", "value": 1, "ts": now.isoformat()},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
        fname = f.name

    try:
        client = get_client()
        with patch("web.app._METRICS_LOG", fname):
            resp = client.get("/api/logs/metrics?hours=24", headers=auth())
        assert resp.status_code == 200
        data = resp.json()["events"]
        assert len(data) == 2
    finally:
        os.unlink(fname)


def test_metrics_filters_by_name():
    now = datetime.now(tz=timezone.utc)
    events = [
        {"type": "timer", "name": "stt_transcribe", "duration_ms": 300, "ts": now.isoformat()},
        {"type": "counter", "name": "session", "value": 1, "ts": now.isoformat()},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
        fname = f.name

    try:
        client = get_client()
        with patch("web.app._METRICS_LOG", fname):
            resp = client.get("/api/logs/metrics?name=stt_transcribe&hours=24", headers=auth())
        assert resp.status_code == 200
        data = resp.json()["events"]
        assert all(e["name"] == "stt_transcribe" for e in data)
        assert len(data) == 1
    finally:
        os.unlink(fname)


def test_metrics_filters_by_time():
    now = datetime.now(tz=timezone.utc)
    old_ts = (now - timedelta(hours=48)).isoformat()
    recent_ts = now.isoformat()
    events = [
        {"type": "timer", "name": "stt_transcribe", "duration_ms": 100, "ts": old_ts},
        {"type": "timer", "name": "stt_transcribe", "duration_ms": 200, "ts": recent_ts},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
        fname = f.name

    try:
        client = get_client()
        with patch("web.app._METRICS_LOG", fname):
            resp = client.get("/api/logs/metrics?hours=24", headers=auth())
        assert resp.status_code == 200
        data = resp.json()["events"]
        # Only the recent one should be returned
        assert len(data) == 1
        assert data[0]["duration_ms"] == 200
    finally:
        os.unlink(fname)


def test_metrics_missing_file():
    client = get_client()
    with patch("web.app._METRICS_LOG", "/nonexistent/metrics.jsonl"):
        resp = client.get("/api/logs/metrics", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["events"] == []


# ── /api/logs/download ──────────────────────────────────────────────


def test_download_rejects_bad_filename():
    client = get_client()
    resp = client.get("/api/logs/download/../../etc/passwd", headers=auth())
    # FastAPI will 404 on path with slashes; either way access is denied
    assert resp.status_code in (400, 404, 422)


def test_download_rejects_non_log_filename():
    client = get_client()
    resp = client.get("/api/logs/download/secrets.txt", headers=auth())
    assert resp.status_code == 400


def test_download_rejects_missing_file():
    client = get_client()
    with patch("web.app._LOG_DIR", "/tmp"):
        resp = client.get("/api/logs/download/2026-01-01.jsonl", headers=auth())
    assert resp.status_code == 404


def test_download_serves_jsonl():
    with tempfile.TemporaryDirectory() as tmpdir:
        fname = "2026-03-15.jsonl"
        fpath = os.path.join(tmpdir, fname)
        with open(fpath, "w") as f:
            f.write('{"type": "session_start"}\n')

        client = get_client()
        with patch("web.app._LOG_DIR", tmpdir):
            resp = client.get(f"/api/logs/download/{fname}", headers=auth())

    assert resp.status_code == 200


def test_download_allows_bender_log():
    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = os.path.join(tmpdir, "bender.log")
        with open(fpath, "w") as f:
            f.write("some log content\n")

        client = get_client()
        with patch("web.app._LOG_DIR", tmpdir):
            resp = client.get("/api/logs/download/bender.log", headers=auth())

    assert resp.status_code == 200


def test_download_requires_pin():
    client = get_client()
    resp = client.get("/api/logs/download/2026-03-15.jsonl")
    assert resp.status_code == 401
