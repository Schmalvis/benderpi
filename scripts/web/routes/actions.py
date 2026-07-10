import asyncio
import json
import os
import subprocess
import sys

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from web.auth import require_token

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

from config import cfg
from logger import get_logger

_IS_LINUX = os.name != "nt"
_audit = get_logger("audit")


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


@router.post("/api/actions/restart")
async def action_restart(request: Request):
    _audit.info("actions.restart from %s", _client_ip(request))
    if not _IS_LINUX:
        return {"status": "ok", "message": "Simulated restart (not on Pi)"}
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["sudo", "systemctl", "restart", "bender-converse"],
            capture_output=True, text=True, timeout=30,
        )
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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


@router.post("/api/actions/toggle-mode")
async def action_toggle_mode(request: Request, body: dict = Body(...)):
    mode = body.get("mode", "").strip()
    if mode not in ("puppet_only", "converse"):
        raise HTTPException(status_code=400, detail="mode must be 'puppet_only' or 'converse'")
    _audit.info("actions.toggle-mode -> %s from %s", mode, _client_ip(request))
    if not _IS_LINUX:
        return {"status": "ok", "mode": mode, "message": "Simulated (not on Pi)"}
    try:
        cmd = ["sudo", "systemctl", "stop" if mode == "puppet_only" else "start", "bender-converse"]
        await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=15,
        )
        return {"status": "ok", "mode": mode}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
