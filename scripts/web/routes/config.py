import asyncio
import json
import os
import re
import subprocess
import sys

from fastapi import APIRouter, Body, Depends, HTTPException
from web.auth import require_pin

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

import leds

_CONFIG_PATH = os.path.join(_BASE_DIR, "bender_config.json")
_WATCHDOG_CONFIG_PATH = os.path.join(_BASE_DIR, "watchdog_config.json")

router = APIRouter(dependencies=[Depends(require_pin)])


def _load_json_file(path: str) -> dict:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json_file(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


@router.get("/api/config")
async def config_get():
    return _load_json_file(_CONFIG_PATH)


@router.put("/api/config")
async def config_put(body: dict = Body(...)):
    current = _load_json_file(_CONFIG_PATH)
    current.update(body)
    _save_json_file(_CONFIG_PATH, current)
    return {"status": "ok", "config": current}


@router.get("/api/config/watchdog")
async def config_watchdog_get():
    return _load_json_file(_WATCHDOG_CONFIG_PATH)


@router.put("/api/config/watchdog")
async def config_watchdog_put(body: dict = Body(...)):
    current = _load_json_file(_WATCHDOG_CONFIG_PATH)
    current.update(body)
    _save_json_file(_WATCHDOG_CONFIG_PATH, current)
    return {"status": "ok", "config": current}


@router.get("/api/config/volume")
async def volume_get():
    result = await asyncio.to_thread(
        subprocess.run,
        ["amixer", "-c", "2", "sget", "Speaker"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Could not read volume")
    match = re.search(r"\[(\d+)%\]", result.stdout)
    if not match:
        raise HTTPException(status_code=500, detail="Could not parse volume")
    return {"level": int(match.group(1))}


@router.post("/api/config/volume")
async def volume_set(body: dict = Body(...)):
    level = body.get("level")
    if level is None or not isinstance(level, (int, float)):
        raise HTTPException(status_code=400, detail="level must be a number")
    level = max(0, min(100, int(level)))
    result = await asyncio.to_thread(
        subprocess.run,
        ["amixer", "-c", "2", "sset", "Speaker", f"{level}%"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Could not set volume")
    return {"status": "ok", "level": level}


@router.post("/api/config/led-brightness")
async def set_led_brightness(body: dict = Body(...)):
    value = float(body.get("brightness", 1.0))
    value = max(0.0, min(1.0, value))
    leds.set_brightness(value)
    return {"brightness": value}
