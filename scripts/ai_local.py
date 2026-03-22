"""Local LLM responder — talks to Ollama for on-device AI responses."""

import requests

from ai_response import BENDER_SYSTEM_PROMPT
from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("ai_local")

HEDGE_PHRASES = {
    "i'm not sure", "i don't know", "as an ai",
    "i cannot", "i can't help", "i'm just a",
    "language model", "i apologize",
}


class QualityCheckFailed(Exception):
    """Raised when local LLM response fails quality check."""

    def __init__(self, reason: str, response_text: str):
        self.reason = reason
        self.response_text = response_text
        super().__init__(f"Quality check failed: {reason}")


def check_response_quality(text: str) -> tuple[bool, str]:
    """Return (passed, reason). Reason is empty string if passed."""
    if len(text.strip()) < 10:
        return False, "too_short"
    text_lower = text.lower()
    for phrase in HEDGE_PHRASES:
        if phrase in text_lower:
            return False, "hedge_phrase"
    return True, ""


class LocalAIResponder:
    """Local LLM responder using Ollama REST API."""

    def __init__(self):
        self.history: list[dict] = []
        self.max_history: int = 6

    def _trim_history(self):
        if len(self.history) > self.max_history * 2:
            self.history = self.history[-(self.max_history * 2):]

    def generate(self, user_text: str) -> str:
        """Generate a response via Ollama. Returns reply text only.

        Raises QualityCheckFailed if response is poor quality.
        Raises requests.exceptions.* on connection/timeout errors.
        """
        self.history.append({"role": "user", "content": user_text})

        with metrics.timer("ai_local_call", model=cfg.local_llm_model):
            resp = requests.post(
                f"{cfg.local_llm_url}/api/chat",
                json={
                    "model": cfg.local_llm_model,
                    "messages": [
                        {"role": "system", "content": BENDER_SYSTEM_PROMPT},
                        *self.history,
                    ],
                    "stream": False,
                    "options": {"num_predict": cfg.ai_max_tokens},
                },
                timeout=cfg.local_llm_timeout,
            )
            resp.raise_for_status()

        reply = resp.json()["message"]["content"].strip()
        self.history.append({"role": "assistant", "content": reply})
        self._trim_history()

        passed, reason = check_response_quality(reply)
        if not passed:
            raise QualityCheckFailed(reason, reply)

        metrics.count("ai_local_success")
        return reply

    def clear_history(self):
        """Call at end of each conversation session."""
        self.history = []
