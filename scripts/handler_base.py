"""Base classes and utilities for intent handlers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from logger import get_logger

log = get_logger("handler_base")


@dataclass
class Response:
    """Standard response object returned by all handlers."""

    text: str               # display text or clip basename
    wav_path: str           # WAV file to play
    method: str             # real_clip | pre_gen_tts | promoted_tts | handler_* | ai_fallback | error_fallback
    intent: str             # classified intent name
    sub_key: str | None = None
    is_temp: bool = False           # caller must os.unlink() after playback
    needs_thinking: bool = False    # True if response generated on-the-fly
    model: str | None = None        # AI model name if ai_fallback


class Handler:
    """Base class for intent handlers.

    Subclasses declare `intents` (list of intent strings they handle)
    and implement `handle()`.
    """

    intents: list[str] = []

    def handle(self, text: str, intent: str, sub_key: str | None = None) -> Response | None:
        """Return a Response, or None to fall through to AI fallback."""
        raise NotImplementedError


def load_clips_from_index(key: str, index_path: str, base_dir: str) -> list[str]:
    """Load clip paths from index.json by key.

    Returns a list of absolute WAV paths, or [] if the key or file is missing.
    Filters out entries where the file doesn't exist on disk.
    Used by both thinking clips and timer alert clips.
    """
    try:
        with open(index_path, "r") as f:
            index = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning("Could not load index %s: %s", index_path, exc)
        return []

    entries = index.get(key, [])
    if not entries:
        return []

    clips = []
    for entry in entries:
        full = os.path.join(base_dir, entry)
        if os.path.exists(full):
            clips.append(full)
    log.info("Loaded %d %s clip(s) (%d missing)", len(clips), key, len(entries) - len(clips))
    return clips
