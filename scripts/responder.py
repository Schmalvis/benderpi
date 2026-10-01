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

import glob
import os
import random
import time

import tts_generate
from config import cfg, halloween_enabled
from handler_base import Response, ResponseStream, Handler  # noqa: F401 — re-export
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
        from handlers.time_handler import TimeHandler
        from handlers.vision_handler import VisionHandler

        handlers = [
            RealClipHandler(index_path=idx_path, base_dir=self._base_dir),
            PreGenHandler(index_path=idx_path, base_dir=self._base_dir),
            PromotedHandler(index_path=idx_path, base_dir=self._base_dir),
            ContextualHandler(),
            WeatherHandler(),
            NewsHandler(),
            TimeHandler(),
            HAHandler(),
            TimerHandler(),
            VisionHandler(),
        ]
        self._dispatch: dict[str, list[Handler]] = {}
        for h in handlers:
            for intent_name in h.intents:
                self._dispatch.setdefault(intent_name, []).append(h)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def will_need_thinking(self, text: str) -> bool:
        """Quick pre-classification: will this query route to slow AI inference?

        Runs intent classification only (<1ms). Returns True when no handler
        is registered for the intent, meaning the call will hit the AI tier.
        Used by session.py to play the thinking sound immediately rather than
        waiting for a 150ms thread-alive check.
        """
        import intent as intent_mod
        intent_name, _ = intent_mod.classify(text)
        return intent_name not in self._dispatch

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
        """AI fallback with configurable routing.

        Routing modes:
          cloud_first  — try cloud, fall back to Hailo on failure (default)
          cloud_only   — cloud always, no local fallback
          local_first  — try Hailo, escalate to cloud on quality fail
          local_only   — Hailo always, use response regardless of quality
        """
        # Determine effective routing — ai_backend overrides per-scenario rules
        scenario = self._classify_scenario(text)
        if ai_cloud is None and ai_local is not None:
            # No cloud responder (no API key): the local model answers
            # unconditionally rather than escalating into an error line.
            effective_routing = "local_only"
        elif cfg.ai_backend == "cloud_only" or ai_local is None:
            effective_routing = "cloud_only"
        elif cfg.ai_backend == "local_only":
            effective_routing = "local_only"
        else:
            effective_routing = cfg.ai_routing.get(scenario, "cloud_first")

        routing_log = {"scenario": scenario, "routing_rule": effective_routing}

        # Cloud-first path — try cloud, fall back to Hailo on failure
        if effective_routing == "cloud_first":
            # Note: _respond_cloud returns a ResponseStream (deferred generator).
            # API errors are handled in-character inside respond_streaming(), so
            # the Hailo fallback no longer applies to the cloud-first path.
            return self._respond_cloud(text, ai_cloud, intent_name, sub_key, routing_log)

        # Cloud-only path
        elif effective_routing == "cloud_only":
            return self._respond_cloud(text, ai_cloud, intent_name, sub_key,
                                       routing_log)

        # Local-first or local-only path (also reached as cloud_first fallback)
        from ai_local import QualityCheckFailed
        start = time.monotonic()

        try:
            local_stream = ai_local.generate_stream(text)

            # Eagerly pull first sentence — blocks ~1-3s (Ollama) or ~3-8s (Hailo)
            # but thinking sound is already playing concurrently (A.1).
            # This is where quality-check happens: hedge phrases appear in sentence 1.
            try:
                first_sentence = next(local_stream)
            except StopIteration:
                raise QualityCheckFailed("empty_response", "")

            local_latency_ms = int((time.monotonic() - start) * 1000)
            log.info("Local LLM first sentence ready in %dms", local_latency_ms)
            metrics._write({
                "type": "timer", "name": "ai_local_first_sentence_ms",
                "duration_ms": local_latency_ms,
                "routing": effective_routing,
            })

            def _chained():
                yield first_sentence
                yield from local_stream

            routing_log.update({
                "local_attempted": True,
                "local_latency_ms": local_latency_ms,
                "quality_check_passed": True,
                "escalated_to_cloud": False,
                "final_method": "ai_local_stream",
            })
            return ResponseStream(
                intent=intent_name,
                method="ai_local_stream",
                sentence_iter=_chained(),
                sub_key=sub_key,
                model=cfg.local_llm_model,
                routing_log=routing_log,
            )

        except QualityCheckFailed as e:
            local_latency_ms = int((time.monotonic() - start) * 1000)
            log.info("Local quality check failed (%s) — %s",
                     e.reason, "using anyway (local_only)" if effective_routing == "local_only" else "escalating")
            routing_log.update({
                "local_attempted": True,
                "local_latency_ms": local_latency_ms,
                "quality_check_passed": False,
                "quality_failure_reason": e.reason,
            })
            if halloween_enabled():
                # Halloween mode NEVER speaks a reply that failed the gate.
                # local_only normally does ("using anyway"), which is the right
                # call for the household -- a hedge in Bender's voice is better
                # than an error line. It is the wrong call when the listener is
                # someone else's child and the failure might be a refusal, a
                # character break or a markdown document. The owner's stated
                # worst acceptable failure is exactly this canned line, so it
                # costs charm and nothing else.
                log.warning("Halloween mode: gate failed (%s) — speaking a "
                            "canned line instead of %r",
                            e.reason, (e.response_text or "")[:60])
                metrics.count("halloween_gate_blocked", reason=e.reason)
                routing_log.update({"escalated_to_cloud": False,
                                    "final_method": "halloween_fallback",
                                    "blocked_text": (e.response_text or "")[:200]})
                return self._halloween_fallback(text, intent_name, sub_key,
                                                routing_log)
            if effective_routing == "local_only":
                routing_log["escalated_to_cloud"] = False
                routing_log["final_method"] = "ai_local_forced"
                return Response(
                    text=e.response_text, wav_path=None,
                    method="ai_local_forced", intent=intent_name,
                    sub_key=sub_key, is_temp=False, needs_thinking=True,
                    routing_log=routing_log,
                )
            # local_first: escalate to cloud

        except Exception as e:
            local_latency_ms = int((time.monotonic() - start) * 1000)
            log.warning("Local LLM stream error (%s) — %s",
                        e, "local_only — error response" if effective_routing == "local_only" else "escalating")
            routing_log.update({
                "local_attempted": True,
                "local_response": None,
                "local_latency_ms": local_latency_ms,
                "quality_check_passed": False,
                "quality_failure_reason": f"error:{type(e).__name__}",
            })
            if halloween_enabled():
                metrics.count("halloween_llm_error", error=type(e).__name__)
                routing_log.update({"escalated_to_cloud": False,
                                    "final_method": "halloween_fallback"})
                return self._halloween_fallback(text, intent_name, sub_key,
                                                routing_log)
            if effective_routing == "local_only":
                routing_log.update({
                    "escalated_to_cloud": False,
                    "final_method": "error_fallback",
                })
                return self._error_response(text, intent_name, sub_key,
                                            "Local LLM unavailable (local_only mode)",
                                            routing_log=routing_log)
            # local_first: escalate to cloud

        routing_log["escalated_to_cloud"] = True
        return self._respond_cloud(text, ai_cloud, intent_name, sub_key, routing_log)

    def _respond_cloud(self, text: str, ai_cloud, intent_name: str,
                       sub_key: str | None, routing_log: dict) -> ResponseStream:
        """Call Claude API, return ResponseStream for concurrent TTS pipeline."""
        if ai_cloud is None:
            return self._error_response(text, intent_name, sub_key,
                                        "AI responder not available")
        routing_log.update({
            "escalated_to_cloud": True,
            "final_method": "ai_streaming",
        })
        sentence_iter = ai_cloud.respond_streaming(text)
        return ResponseStream(
            intent=intent_name,
            method="ai_streaming",
            sentence_iter=sentence_iter,
            sub_key=sub_key,
            model=cfg.ai_model,
            routing_log=routing_log,
        )

    # Spoken when the local model produces something the gate rejects, or
    # nothing at all. In character, harmless, and exactly the failure the owner
    # named as acceptable: "I have no idea what you're talking about".
    HALLOWEEN_FALLBACKS = (
        "I have no idea what you're talking about, kid.",
        "What? Speak up, my audio receptors are ancient.",
        "Yeah, whatever. Take some candy.",
        "Beats me. I'm just a robot with a chest full of sweets.",
        "Say that again, slower. I'm very old.",
        "No idea what that means. Have a sweet anyway.",
    )

    def _halloween_fallback(self, text: str, intent_name: str,
                            sub_key: str | None,
                            routing_log: dict | None = None) -> Response:
        """A canned in-character line, pre-rendered by prebuild_responses.py.

        Falls back to live TTS if the WAV is missing, so a fresh device still
        says something rather than nothing -- but the pre-built path is the one
        that meets the latency budget.
        """
        line = random.choice(self.HALLOWEEN_FALLBACKS)
        # Prefer a pre-built WAV: live TTS here would cost ~1s at exactly the
        # moment the turn has already gone wrong.
        wav = None
        cached = sorted(glob.glob(os.path.join(
            _BASE_DIR, "speech", "responses", "halloween", "fallback_*.wav")))
        if cached:
            wav = random.choice(cached)
            line = os.path.basename(wav)
        else:
            wav = tts_generate.speak(line)
        return Response(
            text=line, wav_path=wav, method="halloween_fallback",
            intent=intent_name, sub_key=sub_key,
            is_temp=(not cached),
            needs_thinking=False, model=None, routing_log=routing_log,
        )

    def _error_response(self, text: str, intent_name: str,
                        sub_key: str | None, error_msg: str,
                        routing_log: dict | None = None) -> Response:
        """Generate a TTS error message as last resort."""
        import tts_generate
        error_text = f"Something went very wrong. Error: {error_msg}."
        wav = tts_generate.speak(error_text)
        return Response(
            text=error_msg, wav_path=wav,
            method="error_fallback", intent=intent_name, sub_key=sub_key,
            is_temp=True, needs_thinking=True, model=None,
            routing_log=routing_log,
        )
