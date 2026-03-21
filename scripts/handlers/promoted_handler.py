"""Handler for promoted AI responses (pre-built WAVs matched by pattern)."""
from __future__ import annotations
import os
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("promoted_handler")


class PromotedHandler(Handler):
    intents = ["PROMOTED"]

    def __init__(self, index_path: str = None, base_dir: str = None):
        _base = base_dir or os.path.join(os.path.dirname(__file__), "..")
        self._base_dir = os.path.normpath(_base)

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        if not sub_key:
            return None
        wav_path = os.path.join(self._base_dir, sub_key)
        if not os.path.isfile(wav_path):
            log.warning("PromotedHandler: missing WAV %s", wav_path)
            return None
        return Response(text=os.path.basename(wav_path), wav_path=wav_path,
                       method="promoted_tts", intent=intent)
