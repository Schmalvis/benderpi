import os
import sys
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from web.auth import require_token

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _SCRIPTS_DIR)

import timers as timers_mod

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/api/timers")
async def list_timers():
    return {"timers": timers_mod.list_timers()}


@router.post("/api/timers")
async def create_timer_api(body: dict = Body(...)):
    label = body.get("label", "timer")
    if "duration_s" in body:
        timer = timers_mod.create_timer(label, float(body["duration_s"]))
        return {"status": "ok", "timer": timer}
    elif "fires_at" in body:
        fires_at = datetime.fromisoformat(body["fires_at"])
        timer = timers_mod.create_alarm(label, fires_at)
        return {"status": "ok", "timer": timer}
    raise HTTPException(status_code=400, detail="Provide duration_s or fires_at")


@router.delete("/api/timers/{timer_id}")
async def cancel_timer_api(timer_id: str):
    if timers_mod.cancel_timer(timer_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Timer not found")


@router.post("/api/timers/{timer_id}/dismiss")
async def dismiss_timer_api(timer_id: str):
    if timers_mod.dismiss_timer(timer_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Timer not found")


@router.post("/api/timers/dismiss-all")
async def dismiss_all_timers():
    count = timers_mod.dismiss_all_fired()
    return {"status": "ok", "dismissed": count}
