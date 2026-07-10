"""Tests for health watchdog."""
import json

def _write_metrics(tmp_path, events):
    path = tmp_path / "metrics.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return str(path)

def test_high_error_rate_triggers_alert(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": "2026-03-29T10:00:00Z", "type": "count", "name": "error", "category": "tts"},
    ] * 10 + [
        {"ts": "2026-03-29T10:00:00Z", "type": "count", "name": "intent", "intent": "GREETING"},
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
        {"ts": "2026-03-29T10:00:00Z", "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 100
    metrics_path = _write_metrics(tmp_path, events)
    config = {"error_rate_threshold": 0.05, "lookback_hours": 168}
    alerts = run_checks(metrics_path=metrics_path, config=config)
    error_alerts = [a for a in alerts if a.severity == "error"]
    assert len(error_alerts) == 0

def test_high_stt_empty_rate(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": "2026-03-29T10:00:00Z", "type": "count", "name": "stt_empty", "pcm_bytes": 100},
    ] * 20 + [
        {"ts": "2026-03-29T10:00:00Z", "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 10
    metrics_path = _write_metrics(tmp_path, events)
    config = {"stt_empty_rate_threshold": 0.10, "lookback_hours": 168}
    alerts = run_checks(metrics_path=metrics_path, config=config)
    stt_alerts = [a for a in alerts if a.check == "stt_empty_rate"]
    assert len(stt_alerts) > 0

def test_high_latency_alert(tmp_path):
    from watchdog import run_checks
    events = [
        {"ts": "2026-03-29T10:00:00Z", "type": "timer", "name": "stt_transcribe", "duration_ms": 5000},
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
        {"ts": "2026-03-29T10:00:00Z", "type": "count", "name": "error", "category": "tts"},
    ] * 10 + [
        {"ts": "2026-03-29T10:00:00Z", "type": "count", "name": "intent", "intent": "GREETING"},
    ] * 10
    config = {"error_rate_threshold": 0.05, "lookback_hours": 168}
    # No metrics_path given -- if run_checks() ignored `events` and tried to
    # load from the default path, this would just see an empty/missing file
    # and return no error_rate alert.
    alerts = run_checks(config=config, events=events)
    error_alerts = [a for a in alerts if a.check == "error_rate"]
    assert len(error_alerts) > 0

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
