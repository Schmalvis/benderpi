import asyncio
import os
import sys

from fastapi import APIRouter, Depends
from web.auth import require_token

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _SCRIPTS_DIR)

router = APIRouter(dependencies=[Depends(require_token)])


@router.get("/api/status")
async def get_status():
    from generate_status import generate_dict
    return await asyncio.to_thread(generate_dict)


@router.post("/api/status/refresh")
async def refresh_status():
    from generate_status import generate_dict, generate
    await asyncio.to_thread(generate)
    return await asyncio.to_thread(generate_dict)
