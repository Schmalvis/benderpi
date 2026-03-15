"""Auto-generate STATUS.md from metrics, logs, and watchdog checks.

Usage: venv/bin/python scripts/generate_status.py
"""

import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone

from logger import get_logger
from watchdog import run_checks, _load_metrics

log = get_logger("status")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_METRICS_PATH = os.path.join(_BASE_DIR, "logs", "metrics.jsonl")
_STATUS_PATH = os.path.join(_BASE_DIR, "STATUS.md")
_LOG_PATH = os.path.join(_BASE_DIR, "logs", "bender.log")


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


def generate():
    events = _load_metrics(_METRICS_PATH, lookback_hours=168)
    alerts = run_checks()

    def avg_timer(name):
        timers = [e for e in events if e.get("type") == "timer" and e.get("name") == name]
        if not timers:
            return "N/A"
        avg = sum(e.get("duration_ms", 0) for e in timers) / len(timers)
        return f"{avg:.0f}ms"

    intent_events = [e for e in events if e.get("type") == "count" and e.get("name") == "intent"]
    total_turns = len(intent_events)
    api_calls = len([e for e in events if e.get("type") == "count" and e.get("name") == "api_call"])
    errors = len([e for e in events if e.get("type") == "count" and e.get("name") == "error"])
    local = total_turns - api_calls - errors if total_turns else 0

    intent_breakdown = Counter(e.get("intent", "?") for e in intent_events)
    top_intents = ", ".join(f"{k} {v}" for k, v in intent_breakdown.most_common(6))

    sessions = [e for e in events if e.get("type") == "count" and e.get("name") == "session" and e.get("event") == "start"]

    alert_lines = []
    for a in alerts:
        icon = {"error": "!!!", "warning": "!", "info": ""}.get(a.severity, "")
        alert_lines.append(f"- {icon} [{a.severity.upper()}] {a.message}")
    if not alert_lines:
        alert_lines = ["- None"]

    recent = _recent_errors()
    error_lines = [f"- {e}" for e in recent[-5:]] if recent else ["- None"]

    git_log = _recent_git_log()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    local_pct = f"{100*local//total_turns}%" if total_turns else "N/A"

    status = f"""# BenderPi Status Report
Generated: {now}

## Health
- Errors (7d): {errors}
- Watchdog alerts: {len([a for a in alerts if a.severity in ('error', 'warning')])}

## Performance (7-day averages)
- STT record: {avg_timer("stt_record")}
- STT transcribe: {avg_timer("stt_transcribe")}
- TTS generation: {avg_timer("tts_generate")}
- API call: {avg_timer("ai_api_call")}
- Audio playback: {avg_timer("audio_play")}
- End-to-end response: {avg_timer("response_total")}

## Usage (7 days)
- Sessions: {len(sessions)} | Turns: {total_turns}
- Local: {local} ({local_pct}) | API: {api_calls} | Errors: {errors}
- Top intents: {top_intents or 'N/A'}

## Attention Needed
{chr(10).join(alert_lines)}

## Recent Errors (from bender.log)
{chr(10).join(error_lines)}

## Recent Changes
{git_log}
"""
    with open(_STATUS_PATH, "w") as f:
        f.write(status)
    log.info("STATUS.md written to %s", _STATUS_PATH)


if __name__ == "__main__":
    generate()
