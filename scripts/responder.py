"""Response priority chain for BenderPi.

Classifies user text and resolves the best response (clip, handler, or AI).

Priority order:
  1. real_clip       — original Bender WAV from speech/wav/
  2. pre_gen_tts     — pre-built TTS from speech/responses/
  3. promoted_tts    — promoted AI response as static WAV
  4. handler         — weather/news/HA control
  5. ai_fallback     — Claude API -> Piper TTS

Usage:
    from responder import Responder
    r = Responder()
    resp = r.get_response(text, ai=ai_instance)
    audio.play(resp.wav_path)
    if resp.is_temp:
        os.unlink(resp.wav_path)
"""

import json
import os
import random
from dataclasses import dataclass

from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("responder")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_INDEX = os.path.join(_BASE_DIR, "speech", "responses", "index.json")


@dataclass
class Response:
    """Result of the response priority chain."""
    text: str                # display text or basename of clip
    wav_path: str            # path to WAV file to play
    method: str              # real_clip | pre_gen_tts | promoted_tts | handler_weather | handler_news | handler_ha | ai_fallback | error_fallback
    intent: str              # classified intent name
    sub_key: str | None      # sub-key for PERSONAL etc.
    is_temp: bool            # True if caller must os.unlink(wav_path) after playback
    needs_thinking: bool     # True if response was generated on the fly (slow)
    model: str | None        # AI model name if ai_fallback, else None


class Responder:
    """Resolves user text to the best Response."""

    def __init__(self, index_path: str = None, base_dir: str = None):
        self._base_dir = base_dir or _BASE_DIR
        idx_path = index_path or _DEFAULT_INDEX
        with open(idx_path) as f:
            self._index = json.load(f)

    # ------------------------------------------------------------------
    # Clip helpers (moved from wake_converse.py)
    # ------------------------------------------------------------------

    def _full_path(self, relative: str) -> str:
        return os.path.join(self._base_dir, relative)

    def pick_clip(self, intent_name: str, sub_key: str = None) -> str | None:
        """Pick a random clip for the given intent, or None if unavailable."""
        if intent_name == "PERSONAL":
            path = self._index.get("personal", {}).get(sub_key)
            return self._full_path(path) if path else None
        clips = self._index.get(intent_name.lower())
        if not clips or not isinstance(clips, list):
            return None
        return self._full_path(random.choice(clips))

    def _is_pre_gen(self, path: str) -> bool:
        """True if the clip is from speech/responses/ (not speech/wav/)."""
        relative = path.replace(self._base_dir, "").replace("\\", "/")
        return "speech/responses" in relative

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

            # --- PROMOTED ---
            if intent_name == "PROMOTED":
                return self._handle_promoted(text, sub_key, ai)

            # --- GREETING / AFFIRMATION / JOKE / PERSONAL / DISMISSAL ---
            if intent_name in ("GREETING", "AFFIRMATION", "JOKE", "PERSONAL", "DISMISSAL"):
                return self._handle_clip(text, intent_name, sub_key, ai)

            # --- WEATHER ---
            if intent_name == "WEATHER":
                return self._handle_weather(text, sub_key, ai)

            # --- NEWS ---
            if intent_name == "NEWS":
                return self._handle_news(text, ai)

            # --- HA_CONTROL ---
            if intent_name == "HA_CONTROL":
                return self._handle_ha(text, ai)

            # --- TIMER ---
            if intent_name == "TIMER":
                return self._respond_handler("timer_set", text, ai, intent_name)

            # --- TIMER_CANCEL ---
            if intent_name == "TIMER_CANCEL":
                return self._respond_handler("timer_cancel", text, ai, intent_name)

            # --- TIMER_STATUS ---
            if intent_name == "TIMER_STATUS":
                return self._respond_handler("timer_status", text, ai, intent_name)

            # --- UNKNOWN -> AI ---
            return self._respond_ai(text, ai)

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    def _handle_promoted(self, text: str, sub_key: str, ai) -> Response:
        clip_path = self._full_path(sub_key)  # sub_key holds the file path
        if os.path.exists(clip_path):
            return Response(
                text=os.path.basename(clip_path), wav_path=clip_path,
                method="promoted_tts", intent="PROMOTED", sub_key=None,
                is_temp=False, needs_thinking=False, model=None,
            )
        # File missing -- fall through to AI
        log.warning("Promoted clip missing: %s — falling back to AI", clip_path)
        return self._respond_ai(text, ai)

    def _handle_clip(self, text: str, intent_name: str, sub_key: str | None, ai) -> Response:
        clip = self.pick_clip(intent_name, sub_key)
        if clip and os.path.exists(clip):
            method = "pre_gen_tts" if self._is_pre_gen(clip) else "real_clip"
            return Response(
                text=os.path.basename(clip), wav_path=clip,
                method=method, intent=intent_name, sub_key=sub_key,
                is_temp=False, needs_thinking=False, model=None,
            )
        # Clip missing -- fall through to AI
        return self._respond_ai(text, ai, intent_name, sub_key)

    def _handle_weather(self, text: str, location: str | None, ai) -> Response:
        try:
            import briefings
            if location:
                wav = briefings.get_weather_wav_for_location(location)
                return Response(
                    text=f"weather_{location}", wav_path=wav,
                    method="handler_weather", intent="WEATHER", sub_key=location,
                    is_temp=True, needs_thinking=True, model=None,
                )
            wav = briefings.get_weather_wav()
            return Response(
                text="weather_briefing", wav_path=wav,
                method="handler_weather", intent="WEATHER", sub_key=None,
                is_temp=False, needs_thinking=False, model=None,
            )
        except Exception as e:
            log.error("Weather handler error: %s", e)
            return self._respond_ai(text, ai, "WEATHER")

    def _handle_news(self, text: str, ai) -> Response:
        try:
            import briefings
            wav = briefings.get_news_wav()
            return Response(
                text="news_briefing", wav_path=wav,
                method="handler_news", intent="NEWS", sub_key=None,
                is_temp=False, needs_thinking=False, model=None,
            )
        except Exception as e:
            log.error("News handler error: %s", e)
            return self._respond_ai(text, ai, "NEWS")

    def _handle_ha(self, text: str, ai) -> Response:
        try:
            from handlers import ha_control
            wav = ha_control.control(text)
            return Response(
                text="ha_control", wav_path=wav,
                method="handler_ha", intent="HA_CONTROL", sub_key=None,
                is_temp=True, needs_thinking=True, model=None,
            )
        except Exception as e:
            log.error("HA control error: %s", e)
            return self._respond_ai(text, ai, "HA_CONTROL")

    def _respond_handler(self, handler_type: str, user_text: str, ai,
                         intent_name: str) -> Response:
        """Dispatch to a named handler and return a Response."""
        try:
            if handler_type == "timer_set":
                from handlers import timer_handler
                wav = timer_handler.handle_set(user_text)
                return Response(
                    text="(timer set)", wav_path=wav,
                    method="handler_timer", intent=intent_name, sub_key=None,
                    is_temp=True, needs_thinking=True, model=None,
                )
            elif handler_type == "timer_cancel":
                from handlers import timer_handler
                wav = timer_handler.handle_cancel(user_text)
                return Response(
                    text="(timer cancel)", wav_path=wav,
                    method="handler_timer", intent=intent_name, sub_key=None,
                    is_temp=True, needs_thinking=True, model=None,
                )
            elif handler_type == "timer_status":
                from handlers import timer_handler
                wav = timer_handler.handle_status(user_text)
                return Response(
                    text="(timer status)", wav_path=wav,
                    method="handler_timer", intent=intent_name, sub_key=None,
                    is_temp=True, needs_thinking=True, model=None,
                )
            else:
                raise ValueError(f"Unknown handler_type: {handler_type!r}")
        except Exception as e:
            log.error("Handler %s error: %s", handler_type, e)
            return self._respond_ai(user_text, ai, intent_name)

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
