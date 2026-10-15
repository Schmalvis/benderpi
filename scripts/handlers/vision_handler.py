"""Handler for vision/scene-awareness responses."""
from __future__ import annotations

import os
import random

import anthropic
import vision
import tts_generate
from ai_response import BENDER_SYSTEM_PROMPT
from config import cfg
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("vision_handler")

_EMPTY_ROOM_RESPONSES = [
    "Scanning the room... nothing there, meat bag. Unless you're invisible, which would actually be impressive.",
    "I don't see anyone. Either the room's empty, or you've finally achieved irrelevance.",
    "Nobody home. Just me and my existential dread.",
]


def _bender_scene_response(scene_description: str) -> str:
    """Ask Bender to react to the scene via a one-shot LLM call (no history)."""
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return scene_description
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=cfg.ai_model,
            max_tokens=100,
            system=BENDER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Tell me what you see: {scene_description}"}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        log.warning("VisionHandler: LLM call failed (%s), using raw description", exc)
        return scene_description


class VisionHandler(Handler):
    intents = ["VISION"]

    def _eyes_are_off(self, intent: str) -> Response:
        """Say the eyes do not work, rather than describe a room.

        The empty-room lines below are a claim about the room. They may only
        be spoken when the camera actually looked and the model actually
        answered. Before 2026-10-15 a failed look reached them, because
        `analyse_scene` returned an identical empty SceneDescription either
        way — so on this device, where the VLM cannot load at all while the
        conversation LLM holds the KV-Cache, "I don't see anyone. Either the
        room's empty, or you've finally achieved irrelevance." was the
        guaranteed answer to "what can you see".
        """
        description = "My eyes aren't working right now. Probably a Tuesday thing."
        wav = tts_generate.speak(description)
        return Response(text=description, wav_path=wav, method="handler_vision",
                        intent=intent, is_temp=True)

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        if not cfg.vlm_enabled:
            log.info("VisionHandler: vlm_enabled is false — not describing the room")
            return self._eyes_are_off(intent)

        try:
            scene = vision.analyse_scene()
        except Exception as exc:
            log.warning("VisionHandler: analyse_scene failed: %s", exc)
            return self._eyes_are_off(intent)

        if scene.failed():
            log.warning("VisionHandler: the camera did not look: %s", scene.error)
            return self._eyes_are_off(intent)

        if scene.is_empty():
            description = random.choice(_EMPTY_ROOM_RESPONSES)
        else:
            description = _bender_scene_response(scene.to_context_string())

        log.info("VisionHandler: %s", description)
        wav = tts_generate.speak(description)
        return Response(text=description, wav_path=wav, method="handler_vision",
                        intent=intent, is_temp=True)
