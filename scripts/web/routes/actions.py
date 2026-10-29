import asyncio
import json
import os
import subprocess
import sys

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from web.auth import require_token
from web.service_guard import ServiceBusy, _start_converse, guard_lock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

from config import cfg
from logger import get_logger

_IS_LINUX = os.name != "nt"
_audit = get_logger("audit")
log = get_logger("web.actions")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"
_VENV_PYTHON = os.path.join(_BASE_DIR, "venv", "bin", "python")
_PREBUILD_SCRIPT = os.path.join(_SCRIPTS_DIR, "prebuild_responses.py")

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/api/actions/session-status")
async def session_status_detail():
    if os.path.exists(cfg.session_file):
        try:
            with open(cfg.session_file) as f:
                return json.load(f)
        except Exception:
            return {"active": False}
    return {"active": False}


@router.post("/api/actions/end-session")
async def end_session(request: Request):
    if not os.path.exists(cfg.session_file):
        return {"status": "no_session"}
    _audit.info("actions.end-session from %s", _client_ip(request))
    with open(cfg.end_session_file, "w") as f:
        f.write("")
    with open(cfg.abort_file, "w") as f:
        f.write("")
    return {"status": "ok"}


@router.get("/api/actions/service-status")
async def service_status():
    if not _IS_LINUX:
        return {"running": False, "uptime": "N/A (not on Pi)"}
    try:
        active = await asyncio.to_thread(
            subprocess.run,
            ["systemctl", "is-active", "bender-converse"],
            capture_output=True, text=True, timeout=5,
        )
        running = active.stdout.strip() == "active"
        uptime_str = "unknown"
        if running:
            ts_result = await asyncio.to_thread(
                subprocess.run,
                ["systemctl", "show", "bender-converse", "--property=ActiveEnterTimestamp"],
                capture_output=True, text=True, timeout=5,
            )
            ts_line = ts_result.stdout.strip()
            if "=" in ts_line:
                ts_val = ts_line.split("=", 1)[1].strip()
                if ts_val:
                    uptime_str = ts_val
        return {"running": running, "uptime": uptime_str}
    except Exception:
        return {"running": False, "uptime": "N/A (not on Pi)"}


# Shown on the phone when systemd will not start Bender. The usual cause is
# StartLimitBurst: 5 starts per 300s, which a few STOP/START presses or one
# Save & Restart after them can use up.
_NOT_STARTED = ("Bender did not start. Usually this is the start limit (5 "
                "starts in 5 minutes): wait 5 minutes, then press START. If it "
                "fails again, he is crashing on start: undo the last config "
                "change, or on the Pi run: journalctl -u bender-converse -n 50")


def _restart_converse_guarded() -> bool:
    """Restart bender-converse. False when systemd would not start it.

    A refused restart falls back to `_start_converse()`, which runs
    reset-failed and retries. The exit code used to be ignored, so Save &
    Restart said "Halloween mode is live" with Bender down (2026-10-29).
    """
    with guard_lock():
        rc = subprocess.run(
            ["sudo", "systemctl", "restart", "bender-converse"],
            capture_output=True, text=True, timeout=30,
        )
        if rc.returncode == 0:
            return True
        log.error("bender-converse restart refused (rc=%s): %s", rc.returncode,
                  (rc.stderr or rc.stdout).strip()[:300])
        return _start_converse()


@router.post("/api/actions/restart")
async def action_restart(request: Request):
    _audit.info("actions.restart from %s", _client_ip(request))
    if not _IS_LINUX:
        return {"status": "ok", "message": "Simulated restart (not on Pi)"}
    try:
        started = await asyncio.to_thread(_restart_converse_guarded)
    except ServiceBusy:
        raise HTTPException(status_code=409, detail="Bender is busy (puppet/vision/mic in use) — try again in a moment")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if not started:
        raise HTTPException(status_code=503, detail=_NOT_STARTED)
    return {"status": "ok"}


@router.post("/api/actions/refresh-briefings")
async def action_refresh_briefings():
    try:
        import briefings
        await asyncio.to_thread(briefings.refresh_all)
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/actions/prebuild")
async def action_prebuild():
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [_VENV_PYTHON, _PREBUILD_SCRIPT],
            capture_output=True, text=True, timeout=120,
            cwd=_BASE_DIR,
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "output": result.stdout + result.stderr,
            "returncode": result.returncode,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/actions/generate-status")
async def action_generate_status():
    from generate_status import generate
    await asyncio.to_thread(generate)
    return {"status": "ok"}


def _end_live_session() -> bool:
    """Ask a running session to stop, and abort anything it is playing.

    Returns True if a session was live. Writes the same two notes the sidebar's
    END button writes (``actions.end_session``), because entering puppet mode
    must not leave a conversation half-running: the puppeteer is about to speak
    AS Bender, and a live session would keep capturing the room and replying
    over the top. The abort note goes first, so playback stops before the
    service guard is contended for.
    """
    live = os.path.exists(cfg.session_file)
    for path in (cfg.abort_file, cfg.end_session_file):
        try:
            with open(path, "w") as f:
                f.write("")
        except OSError as exc:
            log.warning("could not write %s: %s", path, exc)
    return live


def _clear_session_ipc() -> None:
    """Remove the IPC notes once no process is left to consume them.

    ``wake_converse`` clears these at startup, so a stale set is not fatal.
    They are cleared here anyway: ``GET /api/actions/session-status`` reads
    ``cfg.session_file`` directly, so a session file left behind by a SIGTERMed
    process reports a live conversation on a box where nothing is listening.
    """
    for path in (cfg.session_file, cfg.end_session_file, cfg.abort_file):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("could not remove %s: %s", path, exc)


def _toggle_mode_guarded(cmd: list[str]) -> bool:
    """Run the stop or start. False when systemctl failed.

    A start goes through `_start_converse()` for its reset-failed retry. The
    exit code used to be ignored both ways, so a refused START showed a green
    "Bender starting" toast with Bender down, and a failed STOP turned the
    button green while he kept talking (2026-10-29).
    """
    with guard_lock():
        if "start" in cmd:
            return _start_converse()
        rc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if rc.returncode != 0:
            log.error("%s failed (rc=%s): %s", " ".join(cmd), rc.returncode,
                      (rc.stderr or rc.stdout).strip()[:300])
        return rc.returncode == 0


@router.post("/api/actions/toggle-mode")
async def action_toggle_mode(request: Request, body: dict = Body(...)):
    """Enter or leave puppet-only mode.

    Puppet-only means ``bender-converse`` is stopped: no wake word, no open
    mic, no sessions. That is the whole point -- a puppeteer speaking as Bender
    cannot have Bender answering the room at the same time. Entering therefore
    ends any live session FIRST, then stops the service, then clears the IPC
    notes that the stopped process can no longer consume.

    Leaving costs about 23s before the wake word is live again, because Whisper
    and Qwen reload onto the Hailo. That is why puppet playback itself does NOT
    use this route -- it hands the running process a say-request over IPC
    instead (``say_request.py``). This route is for deliberately silencing
    Bender's own ears, not for making him speak.
    """
    mode = body.get("mode", "").strip()
    if mode not in ("puppet_only", "converse"):
        raise HTTPException(status_code=400, detail="mode must be 'puppet_only' or 'converse'")
    _audit.info("actions.toggle-mode -> %s from %s", mode, _client_ip(request))
    puppet = mode == "puppet_only"
    ended = False
    if puppet:
        # Before the guard, not inside it: the abort note is what releases a
        # guard held by an in-flight clip, so taking the guard first could wait
        # out the whole clip.
        ended = await asyncio.to_thread(_end_live_session)
    if not _IS_LINUX:
        if puppet:
            await asyncio.to_thread(_clear_session_ipc)
        return {"status": "ok", "mode": mode, "session_ended": ended,
                "message": "Simulated (not on Pi)"}
    if not puppet:
        # BEFORE the start, not after. wake_converse clears these itself at
        # startup, but the note must not still be on the floor when the first
        # session opens -- a leftover end-session note ends it on turn one.
        await asyncio.to_thread(_clear_session_ipc)
    try:
        cmd = ["sudo", "systemctl", "stop" if puppet else "start", "bender-converse"]
        ok = await asyncio.to_thread(_toggle_mode_guarded, cmd)
    except ServiceBusy:
        raise HTTPException(status_code=409, detail="Bender is busy (puppet/vision/mic in use) — try again in a moment")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    # `is False`, not falsy: a test double that returns None means "ran".
    if ok is False:
        raise HTTPException(status_code=503, detail=(
            "Bender did not stop: systemctl failed. He may still be listening. "
            "On the Pi: sudo systemctl stop bender-converse")
            if puppet else _NOT_STARTED)
    if puppet:
        # AFTER the stop: the process is gone, so nothing is left to consume
        # them, and session-status would otherwise report a live conversation
        # on a box where nothing is listening.
        await asyncio.to_thread(_clear_session_ipc)
    return {"status": "ok", "mode": mode, "session_ended": ended}
