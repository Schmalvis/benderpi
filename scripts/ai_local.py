"""Local LLM responder — Hailo on-chip primary, Ollama CPU fallback."""

import os

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

_HAILO_HEF = "/usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef"


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


class _HailoLLMResponder:
    """On-chip LLM using Qwen2.5-1.5B on Hailo-10H. Lazy-initialised."""

    def __init__(self):
        self._vdevice = None
        self._llm = None
        self._available = None  # None = not yet attempted
        self.history: list[dict] = []

    def _load(self) -> bool:
        if self._available is not None:
            return self._available
        if not os.path.exists(_HAILO_HEF):
            log.warning("Hailo LLM HEF not found: %s", _HAILO_HEF)
            self._available = False
            return False
        try:
            from hailo_platform import VDevice
            from hailo_platform.genai import LLM
            from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID
            params = VDevice.create_params()
            params.group_id = SHARED_VDEVICE_GROUP_ID
            self._vdevice = VDevice(params)
            self._llm = LLM(self._vdevice, _HAILO_HEF)
            self._available = True
            log.info("Hailo LLM ready: Qwen2.5-1.5B on Hailo-10H")
        except Exception as e:
            log.warning("Hailo LLM init failed (%s) — will use Ollama fallback", e)
            self._available = False
        return self._available

    def _trim_history(self):
        if len(self.history) > cfg.ai_max_history * 2:
            self.history = self.history[-(cfg.ai_max_history * 2):]

    def generate(self, user_text: str) -> str:
        if not self._load():
            raise RuntimeError("Hailo LLM not available")

        self.history.append({
            "role": "user",
            "content": [{"type": "text", "text": user_text}],
        })

        messages = [
            {"role": "system", "content": [{"type": "text", "text": BENDER_SYSTEM_PROMPT}]},
            *self.history,
        ]

        with metrics.timer("ai_hailo_call"):
            result = self._llm.generate_all(
                prompt=messages,
                temperature=0.7,
                seed=42,
                max_generated_tokens=cfg.ai_max_tokens,
            )

        # Strip Qwen special tokens
        reply = result.split("<|im_end|>")[0].strip() if result else ""

        self.history.append({
            "role": "assistant",
            "content": [{"type": "text", "text": reply}],
        })
        self._trim_history()

        passed, reason = check_response_quality(reply)
        if not passed:
            raise QualityCheckFailed(reason, reply)

        metrics.count("ai_hailo_success")
        return reply

    def clear_history(self):
        self.history = []


class _OllamaResponder:
    """CPU fallback via Ollama REST API."""

    def __init__(self):
        self.history: list[dict] = []

    def _trim_history(self):
        if len(self.history) > cfg.ai_max_history * 2:
            self.history = self.history[-(cfg.ai_max_history * 2):]

    def generate(self, user_text: str) -> str:
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
        self.history = []


class LocalAIResponder:
    """Local LLM — Hailo on-chip primary, Ollama CPU fallback."""

    def __init__(self):
        self._hailo = _HailoLLMResponder()
        self._ollama = _OllamaResponder()

    def generate(self, user_text: str) -> str:
        """Try Hailo first; fall back to Ollama on unavailability or error."""
        try:
            return self._hailo.generate(user_text)
        except RuntimeError:
            log.info("Hailo LLM unavailable — falling back to Ollama")
            return self._ollama.generate(user_text)
        except Exception as e:
            log.warning("Hailo LLM error (%s) — falling back to Ollama", e)
            return self._ollama.generate(user_text)

    def clear_history(self):
        self._hailo.clear_history()
        self._ollama.clear_history()
