import asyncio
import logging
import os
import subprocess
import sys
import time

from fastapi import APIRouter, BackgroundTasks, Depends
from web.auth import require_token

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _SCRIPTS_DIR)

import audio
import leds
import vision as _vision

_IS_LINUX = os.name != "nt"
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
        was_running = False
        try:
            if _IS_LINUX:
                result = subprocess.run(
                    ["systemctl", "is-active", "bender-converse"],
                    capture_output=True, text=True, timeout=5,
                )
                was_running = result.stdout.strip() == "active"
                if was_running:
                    subprocess.run(
                        ["sudo", "systemctl", "stop", "bender-converse"],
                        capture_output=True, text=True, timeout=15,
                    )
                    time.sleep(0.5)
            leds.set_talking()
            audio.play_stream_oneshot(
                _tts.speak_streaming(t),
                on_chunk=leds.set_level,
                on_done=leds.all_off,
            )
        except Exception as exc:
            log.warning("vision_analyse TTS/audio failed: %s", exc)
        finally:
            if was_running:
                subprocess.run(
                    ["sudo", "systemctl", "start", "bender-converse"],
                    capture_output=True, text=True, timeout=15,
                )

    background_tasks.add_task(_speak_in_background, text)
    return {"text": text, "description": scene.to_context_string()}
