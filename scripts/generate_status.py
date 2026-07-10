"""Auto-generate STATUS.md from metrics, logs, and watchdog checks.

Usage: venv/bin/python scripts/generate_status.py
"""

import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone

from config import cfg
from logger import get_logger
from watchdog import run_checks, _load_metrics

log = get_logger("status")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_METRICS_PATH = os.path.join(_BASE_DIR, "logs", "metrics.jsonl")
_STATUS_PATH = os.path.join(_BASE_DIR, "STATUS.md")
_LOG_PATH = os.path.join(_BASE_DIR, "logs", "bender.log")


def _wake_model_status() -> dict:
    """Report the active wake-word model path and whether it's present on disk.

    Lightweight drift check: this is what the process will actually try to
    load next restart, independent of what bender_config.json says elsewhere
    got patched (e.g. by deploy_hey_bender.sh directly on the Pi).
    """
    path = cfg.oww_model_path
    abs_path = path if os.path.isabs(path) else os.path.join(_BASE_DIR, path)
    return {
        "path": path,
        "exists": os.path.isfile(abs_path),
    }


def _recent_git_log() -> str:
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, cwd=_BASE_DIR,
        )
        return result.stdout.strip() if result.returncode == 0 else "(git log unavailable)"
    except Exception:
        return "(git log unavailable)"


def _recent_errors() -> list[str]:
    errors = []
    try:
        with open(_LOG_PATH) as f:
            for line in f:
                if " ERROR " in line:
                    errors.append(line.strip())
    except FileNotFoundError:
        pass
    return errors[-10:]


def generate_dict() -> dict:
    """Return status data as a structured dict (used by web UI and generate())."""
    events = _load_metrics(_METRICS_PATH, lookback_hours=168)
    alerts = run_checks()

    def avg_timer(name) -> float | None:
        timers = [e for e in events if e.get("type") == "timer" and e.get("name") == name]
        if not timers:
            return None
        return sum(e.get("duration_ms", 0) for e in timers) / len(timers)

    intent_events = [e for e in events if e.get("type") == "count" and e.get("name") == "intent"]
    total_turns = len(intent_events)
    api_calls = len([e for e in events if e.get("type") == "count" and e.get("name") == "api_call"])
    errors = len([e for e in events if e.get("type") == "count" and e.get("name") == "error"])
    local = total_turns - api_calls - errors if total_turns else 0
    local_pct = (100 * local // total_turns) if total_turns else 0

    intent_breakdown = Counter(e.get("intent", "?") for e in intent_events)
    top_intents = dict(intent_breakdown.most_common(6))

    sessions = [e for e in events if e.get("type") == "count" and e.get("name") == "session" and e.get("event") == "start"]

    def count_metric(name) -> int:
        return len([e for e in events if e.get("type") == "count" and e.get("name") == name])

    hailo = {
        # Per-turn HEF reload tax (invisible before the metrics split). In warm
        # mode this fires ~once per session instead of once per AI turn.
        "load_ms": avg_timer("ai_hailo_load"),
        "call_ms": avg_timer("ai_hailo_call"),
        "release_skipped": count_metric("hailo_release_skipped"),
        "busy_lockout": count_metric("hailo_busy_lockout"),
        "lock_stuck": count_metric("hailo_lock_stuck"),
    }

    alert_count = len([a for a in alerts if a.severity in ("error", "warning")])

    recent = _recent_errors()
    wake_model = _wake_model_status()

    return {
        "health": {
            "errors_7d": errors,
            "alert_count": alert_count,
        },
        "wake_model": wake_model,
        "performance": {
            "stt_record_ms": avg_timer("stt_record"),
            "stt_transcribe_ms": avg_timer("stt_transcribe"),
            "tts_generate_ms": avg_timer("tts_generate"),
            "ai_api_call_ms": avg_timer("ai_api_call"),
            "audio_play_ms": avg_timer("audio_play"),
            "response_total_ms": avg_timer("response_total"),
            "ai_hailo_load_ms": hailo["load_ms"],
            "ai_hailo_call_ms": hailo["call_ms"],
        },
        "hailo": hailo,
        "usage": {
            "sessions": len(sessions),
            "turns": total_turns,
            "local": local,
            "api": api_calls,
            "errors": errors,
            "local_pct": local_pct,
            "top_intents": top_intents,
        },
        "alerts": [
            {"severity": a.severity, "check": a.check, "message": a.message, "data": a.data}
            for a in alerts
        ],
        "recent_errors": recent[-5:],
        "git_log": _recent_git_log(),
    }


def generate():
    data = generate_dict()

    def fmt_timer(val):
        return f"{val:.0f}ms" if val is not None else "N/A"

    perf = data["performance"]
    usage = data["usage"]
    alerts_data = data["alerts"]

    alert_lines = []
    for a in alerts_data:
        icon = {"error": "!!!", "warning": "!", "info": ""}.get(a["severity"], "")
        alert_lines.append(f"- {icon} [{a['severity'].upper()}] {a['message']}")
    if not alert_lines:
        alert_lines = ["- None"]

    recent = data["recent_errors"]
    error_lines = [f"- {e}" for e in recent] if recent else ["- None"]

    local_pct = f"{usage['local_pct']}%" if usage["turns"] else "N/A"
    top_intents_str = ", ".join(f"{k} {v}" for k, v in usage["top_intents"].items()) or "N/A"

    wake_model = data["wake_model"]
    wake_model_line = (
        f"- Wake model: `{wake_model['path']}` "
        f"({'found' if wake_model['exists'] else '!!! MISSING — wake loop will exit non-zero at startup'})"
    )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    status = f"""# BenderPi Status Report
Generated: {now}

## Health
- Errors (7d): {usage['errors']}
- Watchdog alerts: {data['health']['alert_count']}
{wake_model_line}

## Performance (7-day averages)
- STT record: {fmt_timer(perf['stt_record_ms'])}
- STT transcribe: {fmt_timer(perf['stt_transcribe_ms'])}
- TTS generation: {fmt_timer(perf['tts_generate_ms'])}
- API call: {fmt_timer(perf['ai_api_call_ms'])}
- Audio playback: {fmt_timer(perf['audio_play_ms'])}
- End-to-end response: {fmt_timer(perf['response_total_ms'])}

## Hailo NPU (LLM)
- HEF load (reload tax): {fmt_timer(data['hailo']['load_ms'])}
- generate_all(): {fmt_timer(data['hailo']['call_ms'])}
- Release skipped: {data['hailo']['release_skipped']} | Busy lockouts: {data['hailo']['busy_lockout']} | Lock stuck: {data['hailo']['lock_stuck']}

## Usage (7 days)
- Sessions: {usage['sessions']} | Turns: {usage['turns']}
- Local: {usage['local']} ({local_pct}) | API: {usage['api']} | Errors: {usage['errors']}
- Top intents: {top_intents_str}

## Attention Needed
{chr(10).join(alert_lines)}

## Recent Errors (from bender.log)
{chr(10).join(error_lines)}

## Recent Changes
{data['git_log']}
"""
    with open(_STATUS_PATH, "w") as f:
        f.write(status)
    log.info("STATUS.md written to %s", _STATUS_PATH)
    if wake_model["exists"]:
        log.info("Active wake model: %s (found)", wake_model["path"])
    else:
        log.error(
            "Active wake model: %s NOT FOUND on disk — wake loop will exit "
            "non-zero at next start. Run scripts/deploy_hey_bender.sh.",
            wake_model["path"],
        )


if __name__ == "__main__":
    generate()
