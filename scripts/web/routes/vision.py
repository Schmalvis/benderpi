import asyncio
import logging
import os
import sys

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from web.auth import require_token
from web.service_guard import ServiceBusy, service_lease

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _SCRIPTS_DIR)

import audio
import leds
import vision as _vision
from config import cfg

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_token)])


@router.post("/api/vision/analyse")
async def vision_analyse(background_tasks: BackgroundTasks):
    """One-shot: look at the room, then speak a reaction to what was seen.

    Refuses rather than speaks when there is nothing real to react to.
    Speaking is expensive and it cannot be taken back: `_speak_in_background`
    takes `service_lease()`, which stops and restarts `bender-converse`, so
    one click costs about 23s of deafness while Whisper and Qwen reload.
    Paying that to voice an invented scene is the worst of both.

    Two refusals, both added 2026-10-15:

    `vlm_enabled` false -> 503. Only `wake_converse.py` read that flag, so
    this route ignored it and tried the VLM anyway. On this device the load
    fails every time: the on-chip KV-Cache is a singleton and the resident
    conversation LLM holds it (measured 2026-07-30, see CLAUDE.md).

    No description -> 502. The route used to prompt the AI with "your camera
    just scanned the room and detected nobody" for BOTH a failed look and an
    empty answer, because `vlm.describe_scene` returned "" for both. So the
    reachable outcome of a click was Bender confidently describing a room
    nothing had looked at. An empty answer is not evidence of an empty room
    either, so neither case gets a fabricated prompt now.
    """
    import tts_generate as _tts
    from ai_response import AIResponder

    if not cfg.vlm_enabled:
        raise HTTPException(status_code=503, detail=(
            "Vision is switched off (vlm_enabled is false). The Hailo "
            "KV-Cache is a singleton and the resident conversation LLM holds "
            "it, so the vision model cannot load while bender-converse runs."))

    scene = await asyncio.to_thread(_vision.analyse_scene)
    if scene.failed():
        raise HTTPException(status_code=502,
                            detail=f"The camera did not look: {scene.error}.")
    if scene.is_empty():
        raise HTTPException(status_code=502, detail=(
            "The camera looked and the model described nothing. That is not "
            "the same as an empty room, so Bender has nothing to react to."))

    prompt = (f"Your camera just scanned the room. {scene.to_context_string()}."
              " React in character.")

    ai = AIResponder()
    text = await asyncio.to_thread(ai.respond, prompt)

    def _speak_in_background(t: str) -> None:
        # Runs in a FastAPI BackgroundTasks worker thread (outside the event
        # loop), so the sync service_lease context manager is used directly.
        # It serialises against puppet playback / mic stream on the single-rate
        # WM8960 and handles the stop/restart of bender-converse.
        try:
            with service_lease():
                leds.set_talking()
                audio.play_stream_oneshot(
                    _tts.speak_streaming(t),
                    on_chunk=leds.set_level,
                    on_done=leds.all_off,
                )
        except ServiceBusy:
            log.info("vision_analyse skipped speaking — audio guard busy")
        except Exception as exc:
            log.warning("vision_analyse TTS/audio failed: %s", exc)

    background_tasks.add_task(_speak_in_background, text)
    return {"text": text, "description": scene.to_context_string()}
