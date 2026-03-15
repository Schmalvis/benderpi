"""Health watchdog — analyses metrics to detect anomalies.

Not a daemon. Called by generate_status.py.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from logger import get_logger

log = get_logger("watchdog")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_METRICS = os.path.join(_BASE_DIR, "logs", "metrics.jsonl")
_DEFAULT_CONFIG = os.path.join(_BASE_DIR, "watchdog_config.json")


@dataclass
class Alert:
    severity: str   # "info" | "warning" | "error"
    check: str
    message: str
    data: dict = field(default_factory=dict)


def _load_config(config_path: str = None) -> dict:
    path = config_path or _DEFAULT_CONFIG
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _load_metrics(path: str, lookback_hours: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    events = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                ts = event.get("ts", "")
                try:
                    if datetime.fromisoformat(ts) >= cutoff:
                        events.append(event)
                except Exception:
                    events.append(event)
    except FileNotFoundError:
        pass
    return events


def run_checks(metrics_path: str = None, config: dict = None) -> list[Alert]:
    """Run all health checks, return list of alerts."""
    cfg = config or _load_config()
    lookback = cfg.get("lookback_hours", 168)
    events = _load_metrics(metrics_path or _DEFAULT_METRICS, lookback)
    alerts = []

    error_counts = [e for e in events if e.get("type") == "count" and e.get("name") == "error"]
    intent_counts = [e for e in events if e.get("type") == "count" and e.get("name") == "intent"]
    stt_empty = [e for e in events if e.get("type") == "count" and e.get("name") == "stt_empty"]
    api_calls = [e for e in events if e.get("type") == "count" and e.get("name") == "api_call"]
    total_turns = len(intent_counts)

    # Error rate
    if total_turns > 0:
        error_rate = len(error_counts) / total_turns
        threshold = cfg.get("error_rate_threshold", 0.05)
        if error_rate > threshold:
            alerts.append(Alert(
                severity="error", check="error_rate",
                message=f"Error rate {error_rate:.0%} exceeds {threshold:.0%} threshold",
                data={"error_rate": error_rate, "errors": len(error_counts), "turns": total_turns},
            ))

    # API fallback rate
    if total_turns > 0:
        api_rate = len(api_calls) / total_turns
        threshold = cfg.get("api_fallback_rate_threshold", 0.20)
        if api_rate > threshold:
            alerts.append(Alert(
                severity="warning", check="api_fallback_rate",
                message=f"API fallback rate {api_rate:.0%} exceeds {threshold:.0%}",
                data={"api_rate": api_rate},
            ))

    # STT empty rate
    stt_total = len(intent_counts) + len(stt_empty)
    if stt_total > 0:
        empty_rate = len(stt_empty) / stt_total
        threshold = cfg.get("stt_empty_rate_threshold", 0.10)
        if empty_rate > threshold:
            alerts.append(Alert(
                severity="warning", check="stt_empty_rate",
                message=f"STT empty rate {empty_rate:.0%} exceeds {threshold:.0%}",
                data={"empty_rate": empty_rate},
            ))

    # Latency checks
    for metric_name, config_key, label in [
        ("stt_transcribe", "stt_latency_threshold_ms", "STT"),
        ("tts_generate", "tts_latency_threshold_ms", "TTS"),
        ("ai_api_call", "api_latency_threshold_ms", "API"),
    ]:
        timers = [e for e in events if e.get("type") == "timer" and e.get("name") == metric_name]
        if timers:
            avg_ms = sum(e.get("duration_ms", 0) for e in timers) / len(timers)
            threshold = cfg.get(config_key, 5000)
            if avg_ms > threshold:
                alerts.append(Alert(
                    severity="warning", check=f"{metric_name}_latency",
                    message=f"{label} avg latency {avg_ms:.0f}ms exceeds {threshold}ms",
                    data={"avg_ms": avg_ms, "samples": len(timers)},
                ))

    return alerts
