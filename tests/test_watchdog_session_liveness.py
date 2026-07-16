"""Offline tests for watchdog.check_session_liveness."""
import json
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "scripts")
import watchdog


# Must match the real conversation_log schema ({"type": "session_start"}),
# NOT {"event": ...} — a prior fixture/code mismatch on this key made the
# check silently always report "no sessions" while the test still passed.
def _write_log(tmp_path, ts):
    p = tmp_path / "2026-05-14.jsonl"
    p.write_text(json.dumps({"type": "session_start", "ts": ts.isoformat()}) + "\n")
    return p


def test_liveness_ok_recent(tmp_path):
    _write_log(tmp_path, datetime.now(timezone.utc) - timedelta(hours=1))
    alerts = watchdog.check_session_liveness(
        {"max_hours_without_session": 6}, logs_dir=str(tmp_path))
    assert alerts == []


def test_liveness_warns_stale(tmp_path):
    _write_log(tmp_path, datetime.now(timezone.utc) - timedelta(hours=10))
    alerts = watchdog.check_session_liveness(
        {"max_hours_without_session": 6}, logs_dir=str(tmp_path))
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].check == "session_liveness"
    # Must be the age-based message, proving the session_start was actually
    # parsed (not the "no session_start events found" fallback that fires when
    # the schema key is misread).
    assert "No session in" in alerts[0].message


def test_liveness_warns_no_logs(tmp_path):
    alerts = watchdog.check_session_liveness(
        {"max_hours_without_session": 6}, logs_dir=str(tmp_path))
    assert len(alerts) == 1
