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
    """Load metrics events within lookback_hours, walking size-rotated
    backups (path.1, path.2, ...) alongside the live file — metrics.py
    rotates path -> path.1 -> path.2 once the live file passes
    MetricsWriter.DEFAULT_MAX_BYTES, so a lookback window that spans a
    rotation would otherwise silently lose its older half.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    events = []
    for candidate in [path] + [f"{path}.{i}" for i in range(1, 10)]:
        try:
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    ts = event.get("ts", "")
                    try:
                        if datetime.fromisoformat(ts) >= cutoff:
                            events.append(event)
                    except Exception:
                        events.append(event)
        except FileNotFoundError:
            if candidate != path:
                break  # backups are contiguous (.1, .2, ...); stop at the first gap
    return events


def _recent(events: list[dict], hours: float) -> list[dict]:
    """Sub-filter an already-loaded event list down to the last `hours`.

    Used for checks that should reflect "is this actively happening" rather
    than the general `lookback_hours` window -- a resolved mic-stall burst
    would otherwise keep re-triggering its alert for the full 7-day window
    it was loaded under.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = []
    for e in events:
        ts = e.get("ts", "")
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                result.append(e)
        except Exception:
            result.append(e)
    return result


def _find_latest_session_start(logs_dir: str):
    """Scan the 3 most recent YYYY-MM-DD.jsonl files for the latest session_start timestamp."""
    from glob import glob
    latest = None
    for path in sorted(glob(os.path.join(logs_dir, "*.jsonl")))[-3:]:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or '"session_start"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("type") != "session_start":
                        continue
                    ts = ev.get("ts")
                    try:
                        dt = datetime.fromisoformat(ts)
                        if latest is None or dt > latest:
                            latest = dt
                    except Exception:
                        continue
        except FileNotFoundError:
            continue
    return latest


def check_session_liveness(cfg: dict, logs_dir: str | None = None) -> list:
    """Return alerts if no session_start event in the last max_hours_without_session hours."""
    max_h = float(cfg.get("max_hours_without_session", 6))
    logs_dir = logs_dir or os.path.join(_BASE_DIR, "logs")
    latest = _find_latest_session_start(logs_dir)
    now = datetime.now(timezone.utc)
    if latest is None:
        return [Alert(
            severity="warning", check="session_liveness",
            message=f"No session_start events found in {logs_dir}",
            data={"logs_dir": logs_dir},
        )]
    age_h = (now - latest).total_seconds() / 3600.0
    if age_h > max_h:
        return [Alert(
            severity="warning", check="session_liveness",
            message=f"No session in {age_h:.1f}h (threshold {max_h:.1f}h)",
            data={"latest_session_start": latest.isoformat(),
                  "age_hours": round(age_h, 2),
                  "threshold_hours": max_h},
        )]
    return []


def _probe_unit_state(unit: str) -> str | None:
    """Return systemd's ActiveState for `unit`, or None if it can't be determined.

    None means "no opinion" (systemctl missing, not a systemd box, call failed)
    and must never be treated as unhealthy — a watchdog that cries wolf when it
    cannot see is worse than one that stays quiet.
    """
    import shutil
    import subprocess

    if not shutil.which("systemctl"):
        return None
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, "-p", "LoadState", "-p", "ActiveState"],
            capture_output=True, text=True, timeout=10,
        )
        # Don't gate on returncode: `show` succeeds even for a dead unit, and
        # for a missing one it still prints LoadState=not-found.
        fields = dict(
            line.split("=", 1)
            for line in (out.stdout or "").splitlines() if "=" in line
        )
    except Exception as exc:                      # pragma: no cover - defensive
        log.debug("systemctl probe failed for %s: %s", unit, exc)
        return None

    # LoadState separates "this box doesn't run the unit" from "the unit is
    # installed and down". Both report ActiveState=inactive, so without this a
    # dev box (or the test suite) would alert on a service it was never meant
    # to run.
    if fields.get("LoadState") != "loaded":
        return None
    return fields.get("ActiveState") or None


# States that mean "not down". `activating`/`reloading` matter because the
# watchdog timer can tick during a deploy restart, and a bounded restart is not
# an outage.
_HEALTHY_UNIT_STATES = {"active", "activating", "reloading"}


def check_service_active(cfg: dict, probe=None) -> list:
    """Alert if the conversation service is not running.

    This is the one check that does not come from metrics, and it exists
    because of a real outage on 2026-07-30: a deploy failed, `git_pull.sh`
    rolled back, and its rollback restart *also* failed (systemd
    StartLimitBurst=5/300s exhausted by repeated restarts during a work
    session). bender-converse sat in `failed` with nothing to report it.
    Nothing else here would have noticed: every other check reads metrics that
    a dead process cannot emit, and `session_liveness` allows 72h of silence by
    design because real usage is sparse. Absence of evidence looked exactly
    like an idle house.

    `probe` is injectable so tests never shell out.
    """
    if not cfg.get("service_liveness_enabled", True):
        return []
    unit = cfg.get("service_liveness_unit", "bender-converse")
    state = (probe or _probe_unit_state)(unit)
    if state is None or state in _HEALTHY_UNIT_STATES:
        return []
    hint = ""
    if state == "failed":
        hint = (" — if this is start-limit-hit, recover with "
                f"`sudo systemctl reset-failed {unit} && sudo systemctl start {unit}`")
    return [Alert(
        severity="error", check="service_inactive",
        message=f"{unit} is {state} — the wake word is not being listened for{hint}",
        data={"unit": unit, "state": state},
    )]


def run_checks(metrics_path: str = None, config: dict = None, events: list[dict] = None) -> list[Alert]:
    """Run all health checks, return list of alerts.

    Pass pre-loaded `events` (e.g. from a caller that already called
    _load_metrics() for its own use, like generate_status.py) to avoid
    parsing metrics.jsonl twice per invocation.
    """
    cfg = config or _load_config()
    lookback = cfg.get("lookback_hours", 168)
    if events is None:
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

    # Briefing generation failures
    briefing_failures = [e for e in events if e.get("type") == "count" and e.get("name") == "briefing_generation_failed"]
    if briefing_failures:
        threshold = cfg.get("briefing_failure_threshold", 3)
        if len(briefing_failures) >= threshold:
            alerts.append(Alert(
                severity="warning", check="briefing_generation_failed",
                message=f"Briefing generation failed {len(briefing_failures)}x in the last {lookback}h — check HA token or network",
                data={"failures": len(briefing_failures)},
            ))

    # Mic stall reinits — the wake loop reinitialised the mic after a stall.
    # A few can happen on legitimate ALSA rate switches; a burst means the USB
    # mic is flapping or wedging repeatedly. Uses its own, shorter lookback
    # (default 24h, not the general `lookback_hours`) so a resolved burst
    # stops paging once it's no longer actively happening, instead of
    # re-triggering the same alert on every 15-min watchdog tick for the
    # rest of the 7-day window it was fixed within.
    mic_stall_lookback = cfg.get("mic_stall_lookback_hours", 24)
    mic_stall_events = _recent(events, mic_stall_lookback)
    stall_reinits = [e for e in mic_stall_events if e.get("type") == "count"
                     and e.get("name") == "wake_loop_stall_reinit"]
    if stall_reinits:
        threshold = cfg.get("mic_stall_reinit_threshold", 3)
        if len(stall_reinits) >= threshold:
            alerts.append(Alert(
                severity="warning", check="mic_stall_reinit",
                message=f"Mic stall reinit {len(stall_reinits)}x in the last {mic_stall_lookback}h "
                        f"(threshold {threshold}) — USB mic may be flapping/wedging",
                data={"reinits": len(stall_reinits), "threshold": threshold},
            ))

    # Mic stall exits — the process exited for a systemd restart after a mic
    # stall it couldn't recover in-process. This usually means the mic needed a
    # physical reseat.
    stall_exits = [e for e in mic_stall_events if e.get("type") == "count"
                   and e.get("name") == "wake_loop_stall_exit"]
    if stall_exits:
        threshold = cfg.get("mic_stall_exit_threshold", 1)
        if len(stall_exits) >= threshold:
            alerts.append(Alert(
                severity="error", check="mic_stall_exit",
                message=f"Mic stall exit {len(stall_exits)}x in the last {mic_stall_lookback}h "
                        f"— service restarted by systemd; mic likely needs a physical reseat",
                data={"exits": len(stall_exits), "threshold": threshold},
            ))

    # Hailo LLM lock stuck — release_chip() skipped its VDevice release N+
    # consecutive times because a generate_all() was still in flight. A zombie
    # inference has wedged the NPU and stranded the shared device; STT on the
    # next turn/session will contend for a device it can't get. Any occurrence
    # is worth surfacing (the responder already emits this only past its own
    # internal threshold).
    lock_stuck = [e for e in events if e.get("type") == "count"
                  and e.get("name") == "hailo_lock_stuck"]
    if lock_stuck:
        threshold = cfg.get("hailo_lock_stuck_threshold", 1)
        if len(lock_stuck) >= threshold:
            alerts.append(Alert(
                severity="error", check="hailo_lock_stuck",
                message=f"Hailo LLM lock stuck {len(lock_stuck)}x in the last {lookback}h "
                        f"— a zombie generate_all() wedged the NPU and stranded the "
                        f"shared VDevice; may need a service restart",
                data={"events": len(lock_stuck), "threshold": threshold},
            ))

    # Missing secrets — Config.validate() logs+counts this at each service
    # startup (wake_converse.py and web/app.py) when a required secret is
    # empty for the active config. Surfaced here so it doesn't sit silently
    # degraded, same failure mode as the 6-day mic outage.
    secrets_missing = [e for e in events if e.get("type") == "count"
                       and e.get("name") == "secrets_missing"]
    if secrets_missing:
        missing_names = sorted({e.get("secret", "?") for e in secrets_missing})
        alerts.append(Alert(
            severity="warning", check="secrets_missing",
            message=f"Missing secret(s) at last startup: {', '.join(missing_names)} "
                    f"— see .env.example for least-privilege setup",
            data={"secrets": missing_names, "events": len(secrets_missing)},
        ))

    alerts.extend(check_session_liveness(cfg))
    # Deliberately last and deliberately not metrics-derived: a dead process
    # emits no metrics, so this is the only check that can see its own absence.
    alerts.extend(check_service_active(cfg))

    return alerts
