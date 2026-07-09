"""ConversationSession — one wake-word-to-goodbye interaction.

Owns: session lifecycle, greeting, turn dispatch, vision injection, audio play,
turn logging, IPC file management.

Caller owns: wake word detection, STT, silence tracking, outer retry loop.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Protocol

import audio
import leds
import tts_generate
from ai_response import AIResponder
from ai_local import LocalAIResponder
from conversation_log import SessionLogger
from handler_base import Response, ResponseStream, load_clips_from_index
from handlers.clip_handler import RealClipHandler
from logger import get_logger
from metrics import metrics
from config import cfg
from responder import Responder

log = get_logger("session")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_PATH = os.path.join(_BASE_DIR, "speech", "responses", "index.json")


# ---------------------------------------------------------------------------
# VisionProvider
# ---------------------------------------------------------------------------

class VisionProvider(Protocol):
    def start_capture(self) -> None: ...
    def get_context(self, *, block: bool = False, timeout: float = 0.0) -> str | None: ...


class FutureVisionProvider:
    """Wraps vision.analyse_scene() in a ThreadPoolExecutor future."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision")
        self._future: Future | None = None
        self._injected = False

    def start_capture(self) -> None:
        import vision
        self._injected = False
        self._future = self._executor.submit(vision.analyse_scene)

    def get_context(self, *, block: bool = False, timeout: float = 0.0) -> str | None:
        if self._injected or self._future is None:
            return None
        try:
            if block:
                scene = self._future.result(timeout=timeout)
            else:
                if not self._future.done():
                    return None
                scene = self._future.result(timeout=0)
        except Exception:
            if block:
                self._injected = True
            return None
        self._injected = True
        return f"[Scene: {scene.to_context_string()}]" if not scene.is_empty() else None


# ---------------------------------------------------------------------------
# TurnResult
# ---------------------------------------------------------------------------

@dataclass
class TurnResult:
    text: str
    intent: str
    method: str
    should_end: bool = False
    end_reason: str | None = None


# ---------------------------------------------------------------------------
# ConversationSession
# ---------------------------------------------------------------------------

class ConversationSession:
    def __init__(
        self,
        *,
        ai: AIResponder,
        ai_local: LocalAIResponder | None = None,
        responder: Responder,
        session_log: SessionLogger,
        vision: VisionProvider | None = None,
        on_audio_chunk: Callable | None = None,
    ) -> None:
        self._ai = ai
        self._ai_local = ai_local
        self._responder = responder
        self._session_log = session_log
        self._vision = vision
        self._on_chunk = on_audio_chunk or (lambda _: None)
        self._thinking_clips: list[str] = load_clips_from_index("thinking", _INDEX_PATH, _BASE_DIR)
        self._greeting_handler = RealClipHandler()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open audio, play greeting, start vision capture, log session_start."""
        metrics.count("session", event="start")
        self._session_log.session_start()
        self._write_session_file(0)
        audio.open_session()

        self._ai.clear_history()
        if self._ai_local:
            self._ai_local.clear_history()

        if cfg.silent_wakeword and cfg.led_listening_enabled:
            log.info("Silent wake word mode — skipping audio greeting")
            self._session_log.log_turn(
                "(wake word)", "GREETING", None, "silent",
                response_text="(silent — LED only)",
            )
        else:
            greeting_resp = self._greeting_handler.handle("(wake word)", "GREETING")
            if greeting_resp:
                leds.set_talking()
                audio.play(greeting_resp.wav_path, on_chunk=self._on_chunk, on_done=leds.all_off)
                self._session_log.log_turn(
                    "(wake word)", "GREETING", None, greeting_resp.method,
                    response_text=os.path.basename(greeting_resp.wav_path),
                )
            else:
                text = "Yo. What do you want?"
                leds.set_talking()
                audio.play_stream(
                    tts_generate.speak_streaming(text),
                    on_chunk=self._on_chunk,
                    on_done=leds.all_off,
                )
                self._session_log.log_turn(
                    "(wake word)", "GREETING", None, "pre_gen_tts", response_text=text,
                )

        # Vision capture starts after greeting to avoid libcamera init stuttering audio
        if self._vision:
            self._vision.start_capture()

    def handle_turn(self, text: str) -> TurnResult:
        """Process one spoken turn. Plays audio. Returns turn metadata."""
        _turn_start = time.monotonic()

        # Non-blocking scene poll — picks it up as soon as it's ready
        self._try_inject_scene(block=False)
        # Force-wait before inference so the current AI call has scene context
        self._try_inject_scene(block=True)

        # Remote end-session check before dispatching
        if os.path.exists(cfg.end_session_file):
            self._cleanup_ipc()
            log.info("Remote end-session: abrupt exit")
            return TurnResult(text=text, intent="REMOTE", method="remote_abrupt",
                              should_end=True, end_reason="remote_abrupt")

        # Run inference on a thread so thinking sound can overlap slow responses
        _resp_holder: list = [None]
        _exc_holder: list = [None]

        def _infer() -> None:
            try:
                _resp_holder[0] = self._responder.get_response(
                    text, self._ai, ai_local=self._ai_local,
                )
            except Exception as exc:
                _exc_holder[0] = exc
            finally:
                audio.abort()  # stops thinking sound if still playing

        _infer_start = time.monotonic()
        _thinking_played = False
        _infer_thread = threading.Thread(target=_infer, daemon=True)

        if self._responder.will_need_thinking(text) and cfg.thinking_sound and self._thinking_clips:
            # AI route — known to be slow; play thinking sound immediately (no 150ms wait)
            _infer_thread.start()
            _thinking_played = True
            audio.play(
                random.choice(self._thinking_clips),
                on_chunk=self._on_chunk,
                on_done=leds.all_off,
            )
        else:
            # Handler/clip route — start thread, check after 150ms
            _infer_thread.start()
            _infer_thread.join(timeout=0.15)
            if _infer_thread.is_alive() and cfg.thinking_sound and self._thinking_clips:
                _thinking_played = True
                audio.play(
                    random.choice(self._thinking_clips),
                    on_chunk=self._on_chunk,
                    on_done=leds.all_off,
                )

        hard_timeout = max(0.0, float(cfg.response_hard_timeout_s) - (time.monotonic() - _infer_start))
        _infer_thread.join(timeout=hard_timeout)
        if _infer_thread.is_alive():
            log.error("Inference exceeded %.1fs hard timeout", float(cfg.response_hard_timeout_s))
            audio.abort()
            metrics.count("inference_hard_timeout")
            err_wav = os.path.join(_BASE_DIR, "speech", "responses", "error_timeout.wav")
            if os.path.exists(err_wav):
                audio.play(err_wav, on_chunk=self._on_chunk, on_done=leds.all_off)
            self._session_log.log_turn(
                text, "TIMEOUT", None, "error_fallback",
                response_text="(inference timeout)", ai_routing=None,
            )
            return TurnResult(text=text, intent="TIMEOUT", method="error_fallback")

        # Release the Hailo LLM's VDevice so STT can reacquire the shared
        # "SHARED"-group device on the next loop iteration (mirrors stt.release()
        # after each transcription). This turn's inference thread has finished
        # (the timeout branch above returns early), but a *previous* turn's
        # abandoned zombie thread may still be inside generate_all(): release_chip()
        # is internally guarded by _HailoLLMResponder._infer_lock and is a safe
        # no-op both while such a zombie holds the lock (device stays held — never
        # released under active inference) and when Hailo never loaded (clip/
        # handler/Ollama turns). The device is freed by a later turn's release once
        # any zombie finishes.
        #
        # In llm_warm_session mode we pass warm=True so this per-turn call keeps
        # the chip resident (no HEF reload next turn); the VDevice is instead
        # released in end(). Gated behind cfg.llm_warm_session (default false)
        # because it assumes Whisper + Qwen HEFs coexist on the Hailo-10H.
        if self._ai_local is not None:
            self._ai_local.release_chip(warm=cfg.llm_warm_session)

        if _exc_holder[0] is not None:
            raise _exc_holder[0]

        response = _resp_holder[0]

        # Hard DISMISSAL fast-path — skip audio, end immediately
        if not isinstance(response, ResponseStream) and response.intent == "DISMISSAL" \
                and cfg.dismissal_ends_session:
            log.info("DISMISSAL: abrupt session end")
            self._discard_temp(response)
            leds.all_off()
            self._session_log.log_turn(
                text, "DISMISSAL", response.sub_key, "abrupt_stop", response.text,
            )
            return TurnResult(text=text, intent="DISMISSAL", method="abrupt_stop",
                              should_end=True, end_reason="dismissal_abrupt")

        # Play response
        resp_text, resp_model, resp_method, resp_routing, response = self._play(
            response, thinking_played=_thinking_played
        )

        # Abort / remote-stop check after playback
        if audio.was_aborted() or os.path.exists(cfg.end_session_file):
            self._cleanup_ipc()
            self._discard_temp(response)
            log.info("Session aborted during playback")
            leds.all_off()
            self._session_log.session_end("aborted")
            metrics.count("session", event="end", turns=self._session_log.turn, reason="aborted")
            self._remove_session_file()
            return TurnResult(text=text, intent="ABORTED", method=resp_method,
                              should_end=True, end_reason="aborted")

        # Soft DISMISSAL — play completed, but don't end session
        if not isinstance(response, ResponseStream) and response.intent == "DISMISSAL":
            self._session_log.log_turn(
                text, "DISMISSAL", response.sub_key, "soft_stop", resp_text,
            )
            return TurnResult(text=text, intent="DISMISSAL", method="soft_stop")

        # Normal turn — log and return
        intent = response.intent if not isinstance(response, ResponseStream) else response.intent
        sub_key = response.sub_key if not isinstance(response, ResponseStream) else response.sub_key
        self._session_log.log_turn(
            text, intent, sub_key, resp_method, resp_text, resp_model, ai_routing=resp_routing,
        )
        self._write_session_file(self._session_log.turn)
        metrics._write({
            "type": "timer", "name": "turn_total",
            "duration_ms": round((time.monotonic() - _turn_start) * 1000, 1),
            "intent": intent, "method": resp_method,
        })

        return TurnResult(text=text, intent=intent, method=resp_method)

    def end(self, reason: str = "timeout") -> None:
        """Close audio, log session_end, clear AI history, clean up IPC files."""
        leds.all_off()
        self._session_log.session_end(reason)
        metrics.count("session", event="end", turns=self._session_log.turn, reason=reason)
        self._remove_session_file()
        audio.close_session()
        if self._ai_local:
            # In warm mode the Hailo VDevice was held across the whole session
            # (per-turn release_chip() was a warm no-op); release it now so it
            # doesn't stay stranded across sessions. warm=False forces the real
            # release, still guarded by _infer_lock — a mid-session zombie leaves
            # the device held, but process-exit close() / the next STT load
            # reclaim it. Harmless in non-warm mode (chip already released
            # per-turn, so this is a no-op clean acquire + null refs).
            self._ai_local.release_chip(warm=False)
            self._ai_local.clear_history()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_inject_scene(self, *, block: bool) -> None:
        if not self._vision:
            return
        ctx = self._vision.get_context(
            block=block,
            timeout=float(cfg.vlm_yolo_timeout_s) if block else 0.0,
        )
        if ctx:
            self._ai.inject_scene_context(ctx)
            if self._ai_local:
                self._ai_local.inject_scene_context(ctx)
            log.info("Vision context injected: %s", ctx)

    def _play(
        self, response, *, thinking_played: bool = False
    ) -> tuple[str, str | None, str, dict | None, object]:
        """Play response audio. Returns (text, model, method, routing_log, response)."""
        if isinstance(response, ResponseStream):
            _collected: list[str] = []
            _first_audio_ts: list[float] = []  # singleton list as mutable cell

            def _collecting_iter(it):
                for sentence in it:
                    _collected.append(sentence)
                    yield sentence

            def _timed_wav_iter(wav_iter):
                for wav_path in wav_iter:
                    if not _first_audio_ts:
                        _first_audio_ts.append(time.monotonic())
                    yield wav_path

            leds.set_talking()
            _play_start = time.monotonic()
            audio.play_stream(
                _timed_wav_iter(
                    tts_generate.speak_from_iter(_collecting_iter(response.sentence_iter))
                ),
                on_chunk=self._on_chunk,
                on_done=leds.all_off,
            )
            if _first_audio_ts:
                metrics._write({
                    "type": "timer", "name": "time_to_first_audio_ms",
                    "duration_ms": round((_first_audio_ts[0] - _play_start) * 1000, 1),
                    "method": response.method,
                })
            return " ".join(_collected), response.model, response.method, response.routing_log, response

        if response.wav_path is None:
            # Synchronous AI — stream TTS directly
            leds.set_talking()
            audio.play_stream(
                tts_generate.speak_streaming(response.text),
                on_chunk=self._on_chunk,
                on_done=leds.all_off,
            )
            return response.text, response.model, response.method, response.routing_log, response

        # Prebuilt wav — play a thinking sound first if handler flagged it and we haven't yet
        if response.needs_thinking and cfg.thinking_sound and self._thinking_clips and not thinking_played:
            audio.play(
                random.choice(self._thinking_clips),
                on_chunk=self._on_chunk,
                on_done=leds.all_off,
            )
        leds.set_talking()
        audio.play(response.wav_path, on_chunk=self._on_chunk, on_done=leds.all_off)
        self._discard_temp(response)
        return response.text, None, response.method, None, response

    def _discard_temp(self, response) -> None:
        if not isinstance(response, ResponseStream) and response.is_temp and response.wav_path:
            try:
                os.unlink(response.wav_path)
            except OSError:
                pass

    def _cleanup_ipc(self) -> None:
        for p in [cfg.end_session_file, cfg.abort_file]:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _write_session_file(self, turns: int) -> None:
        try:
            with open(cfg.session_file, "w") as f:
                json.dump({
                    "active": True,
                    "session_id": self._session_log.session_id,
                    "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "turns": turns,
                }, f)
        except OSError as exc:
            log.warning("Failed to write session file: %s", exc)

    def _remove_session_file(self) -> None:
        for p in [cfg.session_file, cfg.end_session_file]:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
