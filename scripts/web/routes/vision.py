import asyncio
import logging
import os
import sys

from fastapi import APIRouter, BackgroundTasks, Depends
from web.auth import require_token
from web.service_guard import ServiceBusy, service_lease

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _SCRIPTS_DIR)

import audio
import leds
import vision as _vision

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_token)])


@router.post("/api/vision/analyse")
async def vision_analyse(background_tasks: BackgroundTasks):
    import tts_generate as _tts
    from ai_response import AIResponder

    scene = await asyncio.to_thread(_vision.analyse_scene)
    if scene.is_empty():
        prompt = "Your camera just scanned the room and detected nobody. React in character."
    else:
        prompt = f"Your camera just scanned the room. {scene.to_context_string()}. React in character."

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
