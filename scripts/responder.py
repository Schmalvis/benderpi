"""Response priority chain for BenderPi.

Classifies user text and resolves the best response (clip, handler, or AI).

Uses a dispatch table built from handler classes. Each handler declares
which intents it supports; the responder iterates handlers for the
matched intent and returns the first non-None response.

Fallback: AI (Claude API -> Piper TTS).

Usage:
    from responder import Responder
    r = Responder()
    resp = r.get_response(text, ai=ai_instance)
    audio.play(resp.wav_path)
    if resp.is_temp:
        os.unlink(resp.wav_path)
"""

import os

from config import cfg
from handler_base import Response, Handler  # noqa: F401 — re-export
from logger import get_logger
from metrics import metrics

log = get_logger("responder")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_INDEX = os.path.join(_BASE_DIR, "speech", "responses", "index.json")


class Responder:
    """Resolves user text to the best Response."""

    def __init__(self, index_path: str = None, base_dir: str = None):
        self._base_dir = base_dir or _BASE_DIR
        idx_path = index_path or _DEFAULT_INDEX

        # Import handler classes
        from handlers.clip_handler import RealClipHandler
        from handlers.pregen_handler import PreGenHandler
        from handlers.promoted_handler import PromotedHandler
        from handlers.weather_handler import WeatherHandler
        from handlers.news_handler import NewsHandler
        from handlers.ha_handler import HAHandler
        from handlers.timer_handler import TimerHandler

        handlers = [
            RealClipHandler(index_path=idx_path, base_dir=self._base_dir),
            PreGenHandler(index_path=idx_path, base_dir=self._base_dir),
            PromotedHandler(index_path=idx_path, base_dir=self._base_dir),
            WeatherHandler(),
            NewsHandler(),
            HAHandler(),
            TimerHandler(),
        ]
        self._dispatch: dict[str, list[Handler]] = {}
        for h in handlers:
            for intent_name in h.intents:
                self._dispatch.setdefault(intent_name, []).append(h)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _full_path(self, relative: str) -> str:
        return os.path.join(self._base_dir, relative)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def get_response(self, text: str, ai=None) -> Response:
        """Classify text and return the best Response.

        Args:
            text: transcribed user speech
            ai: AIResponder instance (needed for UNKNOWN intent)
        """
        import intent as intent_mod

        with metrics.timer("response_total"):
            intent_name, sub_key = intent_mod.classify(text)
            log.info("Intent: %s%s", intent_name, f" / {sub_key}" if sub_key else "")

            for handler in self._dispatch.get(intent_name, []):
                try:
                    resp = handler.handle(text, intent_name, sub_key)
                    if resp is not None:
                        return resp
                except Exception as exc:
                    log.warning("Handler %s failed for %s: %s",
                                type(handler).__name__, intent_name, exc)

            # No handler matched or all fell through — AI fallback
            return self._respond_ai(text, ai, intent_name, sub_key)

    # ------------------------------------------------------------------
    # AI fallback
    # ------------------------------------------------------------------

    def _respond_ai(self, text: str, ai, intent_name: str = "UNKNOWN",
                    sub_key: str = None) -> Response:
        """Call AI fallback, return Response."""
        if ai is None:
            return self._error_response(text, intent_name, sub_key,
                                        "AI responder not available")
        try:
            wav = ai.respond(text)
            reply = ai.history[-1]["content"] if ai.history else ""
            return Response(
                text=reply, wav_path=wav,
                method="ai_fallback", intent=intent_name, sub_key=sub_key,
                is_temp=True, needs_thinking=True, model=cfg.ai_model,
            )
        except Exception as e:
            log.error("AI fallback error: %s", e)
            return self._error_response(text, intent_name, sub_key, str(e))

    def _error_response(self, text: str, intent_name: str,
                        sub_key: str | None, error_msg: str) -> Response:
        """Generate a TTS error message as last resort."""
        import tts_generate
        error_text = f"Something went very wrong. Error: {error_msg}."
        wav = tts_generate.speak(error_text)
        return Response(
            text=error_msg, wav_path=wav,
            method="error_fallback", intent=intent_name, sub_key=sub_key,
            is_temp=True, needs_thinking=True, model=None,
        )
