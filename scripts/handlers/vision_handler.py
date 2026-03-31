"""Handler for vision/scene-awareness responses."""
from __future__ import annotations

import vision
import tts_generate
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("vision_handler")

_EMPTY_ROOM_RESPONSES = [
    "Scanning the room... nothing there, meat bag. Unless you're invisible, which would actually be impressive.",
    "I don't see anyone. Either the room's empty, or you've finally achieved irrelevance.",
    "Nobody home. Just me and my existential dread.",
]

import random


class VisionHandler(Handler):
    intents = ["VISION"]

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        try:
            scene = vision.analyse_scene()
        except Exception as exc:
            log.warning("VisionHandler: analyse_scene failed: %s", exc)
            description = "My eyes aren't working right now. Probably a Tuesday thing."
            wav = tts_generate.speak(description)
            return Response(text=description, wav_path=wav, method="handler_vision",
                            intent=intent, is_temp=True)

        if scene.is_empty():
            description = random.choice(_EMPTY_ROOM_RESPONSES)
        else:
            # Build description from SceneDescription without LLM
            ctx = scene.to_context_string()
            # ctx is e.g. "[Room: adult male ~35, child female ~8]"
            inner = ctx.replace("[Room: ", "").rstrip("]")
            parts = [p.strip() for p in inner.split(",")]
            if len(parts) == 1:
                description = f"I can see {parts[0]} in the room."
            else:
                joined = ", ".join(parts[:-1]) + " and " + parts[-1]
                description = f"I can see {joined} in the room."

        log.info("VisionHandler: %s", description)
        wav = tts_generate.speak(description)
        return Response(text=description, wav_path=wav, method="handler_vision",
                        intent=intent, is_temp=True)
