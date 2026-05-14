"""Offline tests for watchdog.check_session_liveness."""
import json
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "scripts")
import watchdog


def _write_log(tmp_path, ts):
    p = tmp_path / "2026-05-14.jsonl"
    p.write_text(json.dumps({"event": "session_start", "ts": ts.isoformat()}) + "\n")
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


def test_liveness_warns_no_logs(tmp_path):
    alerts = watchdog.check_session_liveness(
        {"max_hours_without_session": 6}, logs_dir=str(tmp_path))
    assert len(alerts) == 1
