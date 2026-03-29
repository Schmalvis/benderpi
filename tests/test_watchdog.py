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
