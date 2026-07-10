"""Tests for the watchdog alert pusher's cooldown/quiet-hours and retention logic."""
import json
import os
import time
from datetime import datetime, timezone, timedelta

import watchdog_notify as wn


def test_quiet_hours_wraps_midnight_true(monkeypatch):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 23, 30)
    monkeypatch.setattr(wn, "datetime", FakeDT)
    cfg = {"watchdog_quiet_hours_start": 23, "watchdog_quiet_hours_end": 7}
    assert wn._in_quiet_hours(cfg) is True


def test_quiet_hours_wraps_midnight_false(monkeypatch):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 12, 0)
    monkeypatch.setattr(wn, "datetime", FakeDT)
    cfg = {"watchdog_quiet_hours_start": 23, "watchdog_quiet_hours_end": 7}
    assert wn._in_quiet_hours(cfg) is False


def test_quiet_hours_disabled_when_unset():
    assert wn._in_quiet_hours({}) is False


def test_quiet_hours_disabled_when_equal():
    cfg = {"watchdog_quiet_hours_start": 5, "watchdog_quiet_hours_end": 5}
    assert wn._in_quiet_hours(cfg) is False


def test_sweep_old_logs_removes_only_stale_conversation_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(wn, "_LOGS_DIR", str(tmp_path))
    old_log = tmp_path / "2020-01-01.jsonl"
    recent_log = tmp_path / "2026-07-01.jsonl"
    metrics_log = tmp_path / "metrics.jsonl"
    metrics_backup = tmp_path / "metrics.jsonl.1"
    for p in (old_log, recent_log, metrics_log, metrics_backup):
        p.write_text("{}\n")
    old_time = time.time() - 200 * 86400
    os.utime(old_log, (old_time, old_time))
    os.utime(metrics_log, (old_time, old_time))
    os.utime(metrics_backup, (old_time, old_time))

    removed = wn._sweep_old_logs(90)

    assert removed == 1
    assert not old_log.exists()
    assert recent_log.exists()
    assert metrics_log.exists()  # never touched by age-based retention
    assert metrics_backup.exists()


def test_sweep_old_logs_disabled_for_non_positive_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(wn, "_LOGS_DIR", str(tmp_path))
    old_log = tmp_path / "2020-01-01.jsonl"
    old_log.write_text("{}\n")
    old_time = time.time() - 200 * 86400
    os.utime(old_log, (old_time, old_time))
    assert wn._sweep_old_logs(0) == 0
    assert old_log.exists()


def test_state_save_and_load_roundtrip(tmp_path, monkeypatch):
    state_path = tmp_path / ".watchdog_state.json"
    monkeypatch.setattr(wn, "_STATE_PATH", str(state_path))
    state = {"error_rate": {"last_notified": "2026-01-01T00:00:00+00:00", "severity": "error"}}
    wn._save_state(state)
    assert wn._load_state() == state


def test_load_state_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(wn, "_STATE_PATH", str(tmp_path / "nope.json"))
    assert wn._load_state() == {}
