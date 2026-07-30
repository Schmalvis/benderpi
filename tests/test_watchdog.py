"""Tests for health watchdog.

NB: event timestamps MUST be generated relative to now, never hardcoded.
Every check here filters events by `lookback_hours` (168h/7d), so a fixed
timestamp silently ages out of the window and the test stops testing anything.
That is not hypothetical: these tests were written with a literal
"2026-03-29T10:00:00Z" and quietly lost all coverage of the watchdog's alerting
checks in early April 2026 — the "assert len(alerts) > 0" ones started failing,
and worse, `test_no_alerts_when_healthy` kept *passing* vacuously because zero
events trivially produce zero alerts. Found 2026-07-30, ~4 months later.
"""
import json
from datetime import datetime, timedelta, timezone


def _recent_ts(hours_ago: float = 1.0) -> str:
    """A timestamp comfortably inside the default 168h lookback window."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _write_metrics(tmp_path, events):
    path = tmp_path / "metrics.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return str(path)

def test_high_error_rate_triggers_alert(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": _recent_ts(), "type": "count", "name": "error", "category": "tts"},
    ] * 10 + [
        {"ts": _recent_ts(), "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 10
    metrics_path = _write_metrics(tmp_path, events)
    config = {"error_rate_threshold": 0.05, "lookback_hours": 168}
    alerts = run_checks(metrics_path=metrics_path, config=config)
    error_alerts = [a for a in alerts if a.check == "error_rate"]
    assert len(error_alerts) > 0
    assert error_alerts[0].severity == "error"

def test_no_alerts_when_healthy(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": _recent_ts(), "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 100
    metrics_path = _write_metrics(tmp_path, events)
    config = {"error_rate_threshold": 0.05, "lookback_hours": 168}
    alerts = run_checks(metrics_path=metrics_path, config=config)
    error_alerts = [a for a in alerts if a.severity == "error"]
    assert len(error_alerts) == 0

def test_high_stt_empty_rate(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": _recent_ts(), "type": "count", "name": "stt_empty", "pcm_bytes": 100},
    ] * 20 + [
        {"ts": _recent_ts(), "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 10
    metrics_path = _write_metrics(tmp_path, events)
    config = {"stt_empty_rate_threshold": 0.10, "lookback_hours": 168}
    alerts = run_checks(metrics_path=metrics_path, config=config)
    stt_alerts = [a for a in alerts if a.check == "stt_empty_rate"]
    assert len(stt_alerts) > 0

def test_high_latency_alert(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": _recent_ts(), "type": "timer", "name": "stt_transcribe", "duration_ms": 5000},
    ] * 5
    metrics_path = _write_metrics(tmp_path, events)
    config = {"stt_latency_threshold_ms": 4000, "lookback_hours": 168}
    alerts = run_checks(metrics_path=metrics_path, config=config)
    latency_alerts = [a for a in alerts if "latency" in a.check]
    assert len(latency_alerts) > 0

def test_run_checks_accepts_preloaded_events(tmp_path):
    """generate_status.py loads events once and passes them in to avoid
    re-parsing metrics.jsonl a second time inside run_checks()."""
    from watchdog import run_checks
    events = [
        {"ts": _recent_ts(), "type": "count", "name": "error", "category": "tts"},
    ] * 10 + [
        {"ts": _recent_ts(), "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 10
    config = {"error_rate_threshold": 0.05, "lookback_hours": 168}
    # No metrics_path given -- if run_checks() ignored `events` and tried to
    # load from the default path, this would just see an empty/missing file
    # and return no error_rate alert.
    alerts = run_checks(config=config, events=events)
    error_alerts = [a for a in alerts if a.check == "error_rate"]
    assert len(error_alerts) > 0

def test_mic_stall_alert_ages_out_of_short_lookback(tmp_path):
    """A resolved mic-stall burst should stop alerting once it falls outside
    mic_stall_lookback_hours, even though it's still within the general
    168h lookback_hours the rest of the checks use."""
    from watchdog import run_checks
    from datetime import datetime, timezone, timedelta
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    events = [
        {"ts": old_ts, "type": "count", "name": "wake_loop_stall_reinit", "reinit_count": 0},
    ] * 9 + [
        {"ts": old_ts, "type": "count", "name": "wake_loop_stall_exit"},
    ] * 4
    metrics_path = _write_metrics(tmp_path, events)
    config = {"lookback_hours": 168, "mic_stall_lookback_hours": 24,
              "mic_stall_reinit_threshold": 3, "mic_stall_exit_threshold": 1}
    alerts = run_checks(metrics_path=metrics_path, config=config)
    mic_alerts = [a for a in alerts if a.check in ("mic_stall_reinit", "mic_stall_exit")]
    assert mic_alerts == []

def test_mic_stall_alert_fires_within_lookback(tmp_path):
    from watchdog import run_checks
    from datetime import datetime, timezone
    now_ts = datetime.now(timezone.utc).isoformat()
    events = [
        {"ts": now_ts, "type": "count", "name": "wake_loop_stall_reinit", "reinit_count": 0},
    ] * 9 + [
        {"ts": now_ts, "type": "count", "name": "wake_loop_stall_exit"},
    ] * 4
    metrics_path = _write_metrics(tmp_path, events)
    config = {"lookback_hours": 168, "mic_stall_lookback_hours": 24,
              "mic_stall_reinit_threshold": 3, "mic_stall_exit_threshold": 1}
    alerts = run_checks(metrics_path=metrics_path, config=config)
    mic_alerts = {a.check for a in alerts}
    assert "mic_stall_reinit" in mic_alerts
    assert "mic_stall_exit" in mic_alerts

def test_load_metrics_walks_rotated_backups(tmp_path):
    from watchdog import _load_metrics
    from datetime import datetime, timezone
    live = tmp_path / "metrics.jsonl"
    backup1 = tmp_path / "metrics.jsonl.1"
    now = datetime.now(timezone.utc).isoformat()
    live.write_text(json.dumps({"ts": now, "type": "count", "name": "live_event"}) + "\n")
    backup1.write_text(json.dumps({"ts": now, "type": "count", "name": "backup_event"}) + "\n")
    events = _load_metrics(str(live), lookback_hours=168)
    names = {e["name"] for e in events}
    assert names == {"live_event", "backup_event"}

def test_load_metrics_ignores_stale_backup_beyond_cutoff(tmp_path):
    from watchdog import _load_metrics
    from datetime import datetime, timezone, timedelta
    live = tmp_path / "metrics.jsonl"
    backup1 = tmp_path / "metrics.jsonl.1"
    recent = datetime.now(timezone.utc).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
    live.write_text(json.dumps({"ts": recent, "type": "count", "name": "live_event"}) + "\n")
    backup1.write_text(json.dumps({"ts": stale, "type": "count", "name": "old_event"}) + "\n")
    events = _load_metrics(str(live), lookback_hours=168)
    names = {e["name"] for e in events}
    assert names == {"live_event"}


# ---------------------------------------------------------------------------
# Service liveness — the only non-metrics check
# ---------------------------------------------------------------------------

class TestServiceActive:
    """A dead process emits no metrics, so every other check goes quiet exactly
    when things are worst. This check exists because of a real 2026-07-30
    outage: a failed deploy plus a failed rollback restart (systemd
    StartLimitBurst exhausted) left bender-converse in `failed` with nothing to
    report it, and session_liveness tolerates 72h of silence by design."""

    def test_alerts_when_failed(self):
        from watchdog import check_service_active
        alerts = check_service_active({}, probe=lambda unit: "failed")
        assert len(alerts) == 1
        assert alerts[0].check == "service_inactive"
        assert alerts[0].severity == "error"
        assert alerts[0].data["state"] == "failed"

    def test_failed_message_includes_recovery_hint(self):
        """start-limit-hit is the likely cause and reset-failed is the cure —
        an alert that doesn't say so costs the reader a search."""
        from watchdog import check_service_active
        alerts = check_service_active({}, probe=lambda unit: "failed")
        assert "reset-failed" in alerts[0].message

    def test_alerts_when_inactive(self):
        from watchdog import check_service_active
        alerts = check_service_active({}, probe=lambda unit: "inactive")
        assert len(alerts) == 1
        assert alerts[0].data["state"] == "inactive"

    def test_quiet_when_active(self):
        from watchdog import check_service_active
        assert check_service_active({}, probe=lambda unit: "active") == []

    def test_quiet_while_restarting(self):
        """The 15-min timer can tick during a deploy restart; a bounded restart
        is not an outage."""
        from watchdog import check_service_active
        assert check_service_active({}, probe=lambda unit: "activating") == []
        assert check_service_active({}, probe=lambda unit: "reloading") == []

    def test_quiet_when_probe_has_no_opinion(self):
        """None = can't tell (no systemctl, unit not installed on this box).
        A watchdog that alerts when blind is worse than one that stays quiet."""
        from watchdog import check_service_active
        assert check_service_active({}, probe=lambda unit: None) == []

    def test_respects_disable_flag(self):
        from watchdog import check_service_active
        cfg = {"service_liveness_enabled": False}
        assert check_service_active(cfg, probe=lambda unit: "failed") == []

    def test_unit_name_is_configurable(self):
        from watchdog import check_service_active
        seen = []
        check_service_active({"service_liveness_unit": "custom-unit"},
                             probe=lambda unit: seen.append(unit) or "active")
        assert seen == ["custom-unit"]

    def test_probe_returns_none_for_uninstalled_unit(self):
        """LoadState, not ActiveState, distinguishes 'not installed here' from
        'installed and down' — both report ActiveState=inactive."""
        from watchdog import _probe_unit_state
        assert _probe_unit_state("definitely-not-a-real-unit-xyz") is None

    def test_run_checks_includes_service_check(self, tmp_path):
        """Wired into run_checks, not just callable in isolation."""
        from watchdog import run_checks
        metrics_path = _write_metrics(tmp_path, [
            {"ts": _recent_ts(), "type": "count", "name": "intent", "intent": "GREETING"},
        ])
        alerts = run_checks(metrics_path=metrics_path,
                            config={"lookback_hours": 168})
        # On a box without the unit installed the probe has no opinion, so this
        # asserts wiring + no false positive rather than a specific verdict.
        assert not [a for a in alerts if a.check == "service_inactive"]
