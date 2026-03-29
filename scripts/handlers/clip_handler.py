"""Handler for original Bender WAV clips (real_clip responses)."""
from __future__ import annotations
import json, os, random
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("clip_handler")


class RealClipHandler(Handler):
    intents = ["GREETING", "AFFIRMATION", "DISMISSAL", "JOKE"]

    def __init__(self, index_path: str = None, base_dir: str = None):
        _base = base_dir or os.path.join(os.path.dirname(__file__), "..", "..")
        self._base_dir = os.path.normpath(_base)
        _idx = index_path or os.path.join(self._base_dir, "speech", "responses", "index.json")
        self._index = self._load_index(_idx)

    def _load_index(self, path: str) -> dict:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            log.warning("RealClipHandler: could not load index: %s", exc)
            return {}

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        key = intent.lower()
        clips = self._index.get(key, [])
        if not clips:
            return None
        entry = random.choice(clips)
        rel_path = entry["file"] if isinstance(entry, dict) else entry
        wav_path = os.path.join(self._base_dir, rel_path)
        if not os.path.isfile(wav_path):
            log.warning("RealClipHandler: missing WAV %s", wav_path)
            return None
        return Response(text=os.path.basename(wav_path), wav_path=wav_path,
                       method="real_clip", intent=intent, sub_key=sub_key)
