"""Handler for pre-generated TTS responses (PERSONAL intents)."""
from __future__ import annotations
import json, os, random
from handler_base import Handler, Response
from logger import get_logger

log = get_logger("pregen_handler")


class PreGenHandler(Handler):
    intents = ["PERSONAL"]

    def __init__(self, index_path: str = None, base_dir: str = None):
        _base = base_dir or os.path.join(os.path.dirname(__file__), "..")
        self._base_dir = os.path.normpath(_base)
        _idx = index_path or os.path.join(self._base_dir, "speech", "responses", "index.json")
        self._index = self._load_index(_idx)

    def _load_index(self, path: str) -> dict:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            log.warning("PreGenHandler: could not load index: %s", exc)
            return {}

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        personal = self._index.get("personal", {})
        if not sub_key or sub_key not in personal:
            return None
        entry = personal[sub_key]
        if not entry:
            return None
        # index.json stores personal values as strings, lists, or dicts with file+label
        if isinstance(entry, dict):
            rel_path = entry.get("file", "")
        elif isinstance(entry, list):
            rel_path = random.choice(entry)
        else:
            rel_path = entry
        wav_path = os.path.join(self._base_dir, rel_path)
        if not os.path.isfile(wav_path):
            log.warning("PreGenHandler: missing WAV %s", wav_path)
            return None
        return Response(text=os.path.basename(wav_path), wav_path=wav_path,
                       method="pre_gen_tts", intent=intent, sub_key=sub_key)
