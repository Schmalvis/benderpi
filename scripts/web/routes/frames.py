"""Turn frames: status only.

No route here serves a frame, and none takes a path or a name. The frames are
read from disk by the owner, not through this server.
"""
import asyncio
import os
import sys

from fastapi import APIRouter, Depends
from web.auth import require_token

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _SCRIPTS_DIR)

import turn_frames

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/api/frames/status")
async def frames_status():
    # status() reads the disk and the worker thread. It never touches camera.
    return await asyncio.to_thread(turn_frames.status)
