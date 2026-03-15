#!/usr/bin/env python3
"""
Conversation logger — writes JSON-Lines to logs/YYYY-MM-DD.jsonl

Each line is one event:
  type=session_start  — session begins (wake word detected)
  type=turn           — one exchange (user speaks, Bender responds)
  type=session_end    — session closes (dismissal or timeout)

Response methods:
  real_clip       — original Bender WAV from speech/wav/
  pre_gen_tts     — pre-generated TTS from speech/responses/<category>/
  promoted_tts    — AI response promoted to static (speech/responses/promoted/)
  handler_weather — live HA weather fetch + TTS
  handler_news    — cached BBC news briefing + TTS
  handler_ha      — HA confirmation handler + TTS
  ai_fallback     — called Claude API
  error_fallback  — exception during response generation
"""

import json
import os
import uuid
from datetime import datetime, timezone

from logger import get_logger

log = get_logger("conversation_log")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR  = os.path.join(BASE_DIR, "logs")


def _log_path() -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"{date}.jsonl")


def _write(record: dict):
    os.makedirs(LOG_DIR, exist_ok=True)
    record["ts"] = datetime.now(timezone.utc).isoformat()
    with open(_log_path(), "a") as f:
        f.write(json.dumps(record) + "\n")


class SessionLogger:
    def __init__(self):
        self.session_id = str(uuid.uuid4())[:8]
        self.turn = 0

    def session_start(self):
        _write({"type": "session_start", "session_id": self.session_id})

    def session_end(self, reason: str = "timeout"):
        _write({"type": "session_end", "session_id": self.session_id,
                "turns": self.turn, "reason": reason})

    def log_turn(self, user_text: str, intent: str, sub_key: str | None,
                 method: str, response_text: str = "", model: str | None = None):
        self.turn += 1
        _write({
            "type":          "turn",
            "session_id":    self.session_id,
            "turn":          self.turn,
            "user_text":     user_text,
            "intent":        intent,
            "sub_key":       sub_key,
            "method":        method,
            "response_text": response_text,
            "model":         model,
        })
