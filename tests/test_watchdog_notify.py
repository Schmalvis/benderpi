"""Tests for the watchdog alert pusher's cooldown/quiet-hours and retention logic."""
import json
import os
import time
from datetime import datetime, timezone, timedelta

import watchdog_notify as wn
from watchdog import Alert


def _alert(check="mic_stall_reinit", severity="warning", message="x"):
    return Alert(severity=severity, check=check, message=message, data={})


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


# --- managed persistent_notification card (update-in-place + auto-dismiss) ---

_PERSIST_CFG = {"watchdog_notify_domain": "persistent_notification",
                "watchdog_notify_service": "create"}


def test_persistent_card_upserts_with_stable_id(tmp_path, monkeypatch):
    monkeypatch.setattr(wn, "_STATE_PATH", str(tmp_path / "s.json"))
    pushes = []
    monkeypatch.setattr(wn, "_push_ha_notification",
                        lambda cfg, title, message, notification_id=None:
                        pushes.append(notification_id) or True)
    state = {}
    rc = wn._run_persistent_card(
        _PERSIST_CFG, [_alert(), _alert(check="error_rate", severity="error")], state)
    assert rc == 0
    assert pushes == [wn._NOTIF_ID]       # single card, stable id
    assert state.get("card_active") is True


def test_persistent_card_dismisses_when_all_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(wn, "_STATE_PATH", str(tmp_path / "s.json"))
    dismisses = []
    monkeypatch.setattr(wn, "_dismiss_ha_notification",
                        lambda cfg, nid: dismisses.append(nid) or True)
    monkeypatch.setattr(wn, "_push_ha_notification",
                        lambda *a, **k: pytest_fail_no_push())
    state = {"card_active": True}
    rc = wn._run_persistent_card(_PERSIST_CFG, [], state)
    assert rc == 0
    assert dismisses == [wn._NOTIF_ID]
    assert "card_active" not in state     # cleared so we don't re-dismiss


def test_persistent_card_no_dismiss_when_no_card_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(wn, "_STATE_PATH", str(tmp_path / "s.json"))
    dismisses = []
    monkeypatch.setattr(wn, "_dismiss_ha_notification",
                        lambda cfg, nid: dismisses.append(nid) or True)
    rc = wn._run_persistent_card(_PERSIST_CFG, [], {})
    assert rc == 0
    assert dismisses == []                # nothing was up, nothing to dismiss


def pytest_fail_no_push():
    raise AssertionError("must not push when clearing")


def test_push_notify_respects_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(wn, "_STATE_PATH", str(tmp_path / "s.json"))
    pushes = []
    monkeypatch.setattr(wn, "_push_ha_notification",
                        lambda cfg, title, message, notification_id=None:
                        pushes.append(title) or True)
    now = datetime.now(timezone.utc)
    state = {"mic_stall_reinit": {"last_notified": now.isoformat(), "severity": "warning"}}
    rc = wn._run_push_notify(
        {"watchdog_notify_domain": "notify", "watchdog_renotify_hours": 12},
        [_alert()], state)
    assert rc == 0
    assert pushes == []                   # within cooldown, not re-pushed


def _fake_urlopen_capture(captured):
    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode())
        return FakeResp()
    return fake_urlopen


def test_push_includes_notification_id_for_persistent(monkeypatch):
    captured = {}
    monkeypatch.setattr(wn.urllib.request, "urlopen", _fake_urlopen_capture(captured))
    monkeypatch.setattr(wn.cfg, "ha_url", "http://ha.local:8123")
    monkeypatch.setattr(wn.cfg, "ha_token", "tok")
    monkeypatch.setattr(wn.cfg, "http_timeout_s", 5)
    ok = wn._push_ha_notification(_PERSIST_CFG, "t", "m", notification_id="bender_watchdog")
    assert ok is True
    assert captured["data"]["notification_id"] == "bender_watchdog"


def test_push_omits_notification_id_for_notify_domain(monkeypatch):
    captured = {}
    monkeypatch.setattr(wn.urllib.request, "urlopen", _fake_urlopen_capture(captured))
    monkeypatch.setattr(wn.cfg, "ha_url", "http://ha.local:8123")
    monkeypatch.setattr(wn.cfg, "ha_token", "tok")
    monkeypatch.setattr(wn.cfg, "http_timeout_s", 5)
    ok = wn._push_ha_notification(
        {"watchdog_notify_domain": "notify", "watchdog_notify_service": "mobile"},
        "t", "m", notification_id="bender_watchdog")
    assert ok is True
    assert "notification_id" not in captured["data"]


def test_dismiss_noop_for_non_persistent_domain(monkeypatch):
    # Must not even attempt an HTTP call for a notify.* domain.
    monkeypatch.setattr(wn.urllib.request, "urlopen",
                        lambda *a, **k: pytest_fail_no_push())
    assert wn._dismiss_ha_notification({"watchdog_notify_domain": "notify"}, "x") is False
