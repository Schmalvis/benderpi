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
import time

import tts_generate
from config import cfg
from handler_base import Response, Handler  # noqa: F401 — re-export
from logger import get_logger
from metrics import metrics

log = get_logger("responder")

KNOWLEDGE_SIGNALS = {"who ", "what year", "when did", "where is",
                     "how many", "how far", "capital of", "invented",
                     "how does", "what is the", "explain", "define"}

CREATIVE_SIGNALS = {"tell me a joke", "sing", "insult", "roast",
                    "impression", "story", "poem", "rap"}

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
        from handlers.contextual_handler import ContextualHandler

        handlers = [
            RealClipHandler(index_path=idx_path, base_dir=self._base_dir),
            PreGenHandler(index_path=idx_path, base_dir=self._base_dir),
            PromotedHandler(index_path=idx_path, base_dir=self._base_dir),
            ContextualHandler(),
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
    # Main entry point
    # ------------------------------------------------------------------

    def _classify_scenario(self, text: str) -> str:
        """Classify query into scenario for AI routing."""
        t = text.lower()
        if any(s in t for s in KNOWLEDGE_SIGNALS):
            return "knowledge"
        if any(s in t for s in CREATIVE_SIGNALS):
            return "creative"
        return "conversation"

    def get_response(self, text: str, ai=None, ai_local=None) -> Response:
        """Classify text and return the best Response.

        Args:
            text: transcribed user speech
            ai: AIResponder instance (Claude cloud fallback)
            ai_local: LocalAIResponder instance (local LLM, optional)
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

            # No handler matched — AI fallback with local-first routing
            return self._respond_ai(text, ai, intent_name, sub_key, ai_local)

    # ------------------------------------------------------------------
    # AI fallback with hybrid routing
    # ------------------------------------------------------------------

    def _respond_ai(self, text: str, ai_cloud, intent_name: str = "UNKNOWN",
                    sub_key: str | None = None, ai_local=None) -> Response:
        """AI fallback with local-first routing."""
        # Determine effective routing — ai_backend overrides per-scenario rules
        scenario = self._classify_scenario(text)
        if cfg.ai_backend == "cloud_only" or ai_local is None:
            effective_routing = "cloud_only"
        elif cfg.ai_backend == "local_only":
            effective_routing = "local_only"
        else:
            effective_routing = cfg.ai_routing.get(scenario, "local_first")

        routing_log = {"scenario": scenario, "routing_rule": effective_routing}

        # Cloud-only path
        if effective_routing == "cloud_only":
            return self._respond_cloud(text, ai_cloud, intent_name, sub_key,
                                       routing_log)

        # Local-first or local-only path
        from ai_local import QualityCheckFailed
        local_response_text = None
        start = time.monotonic()
        try:
            local_response_text = ai_local.generate(text)
            local_latency_ms = int((time.monotonic() - start) * 1000)

            wav = tts_generate.speak(local_response_text)
            routing_log.update({
                "local_attempted": True,
                "local_response": local_response_text,
                "local_latency_ms": local_latency_ms,
                "quality_check_passed": True,
                "escalated_to_cloud": False,
                "final_method": "ai_local",
            })
            return Response(
                text=local_response_text, wav_path=wav,
                method="ai_local", intent=intent_name, sub_key=sub_key,
                is_temp=True, needs_thinking=True, routing_log=routing_log)

        except QualityCheckFailed as qcf:
            local_latency_ms = int((time.monotonic() - start) * 1000)
            local_response_text = qcf.response_text
            log.info("Local LLM quality check failed (%s), escalating",
                     qcf.reason)
            routing_log.update({
                "local_attempted": True,
                "local_response": qcf.response_text,
                "local_latency_ms": local_latency_ms,
                "quality_check_passed": False,
                "quality_failure_reason": qcf.reason,
            })

        except Exception as e:
            local_latency_ms = int((time.monotonic() - start) * 1000)
            log.warning("Local LLM error: %s, escalating", e)
            routing_log.update({
                "local_attempted": True,
                "local_response": None,
                "local_latency_ms": local_latency_ms,
                "quality_check_passed": False,
                "quality_failure_reason": f"error:{type(e).__name__}",
            })

        # Escalate to cloud (unless local_only)
        if effective_routing == "local_only":
            if local_response_text:
                # Intentional: TTS for rejected response —
                # local_only means use it regardless of quality check outcome.
                wav = tts_generate.speak(local_response_text)
                routing_log.update({
                    "escalated_to_cloud": False,
                    "final_method": "ai_local_forced",
                })
                return Response(
                    text=local_response_text, wav_path=wav,
                    method="ai_local_forced", intent=intent_name,
                    sub_key=sub_key, is_temp=True, needs_thinking=True,
                    routing_log=routing_log)
            else:
                # local_only but local failed entirely — return error
                routing_log.update({
                    "escalated_to_cloud": False,
                    "final_method": "error_fallback",
                })
                return self._error_response(text, intent_name, sub_key,
                                            "Local LLM unavailable (local_only mode)")

        return self._respond_cloud(text, ai_cloud, intent_name, sub_key,
                                   routing_log)

    def _respond_cloud(self, text: str, ai_cloud, intent_name: str,
                       sub_key: str | None, routing_log: dict) -> Response:
        """Call Claude API fallback, return Response."""
        if ai_cloud is None:
            return self._error_response(text, intent_name, sub_key,
                                        "AI responder not available")
        try:
            start = time.monotonic()
            wav = ai_cloud.respond(text)
            cloud_latency_ms = int((time.monotonic() - start) * 1000)
            reply = ai_cloud.history[-1]["content"] if ai_cloud.history else ""
            routing_log.update({
                "escalated_to_cloud": True,
                "cloud_response": reply,
                "cloud_latency_ms": cloud_latency_ms,
                "final_method": "ai_fallback",
            })
            return Response(
                text=reply, wav_path=wav,
                method="ai_fallback", intent=intent_name, sub_key=sub_key,
                is_temp=True, needs_thinking=True, model=cfg.ai_model,
                routing_log=routing_log,
            )
        except Exception as e:
            log.error("AI cloud fallback error: %s", e)
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
