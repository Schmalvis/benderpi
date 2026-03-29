#!/usr/bin/env python3
"""
Bender conversational mode -- thin orchestrator.

Flow:
  1. Wait for "Hey Bender" wake word
  2. Play greeting clip
  3. Listen -> STT -> responder.get_response() -> play
  4. Log every turn to logs/YYYY-MM-DD.jsonl
  5. Loop until silence timeout or DISMISSAL

Response priority chain lives in responder.py.
"""

import json
import os
import random
import time
import sys
import struct

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR   = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

# Load .env
from dotenv import dotenv_values
_env = dotenv_values(os.path.join(BASE_DIR, ".env"))
os.environ.update({k: v for k, v in _env.items() if v})

import pvporcupine
import pyaudio

import audio
import tts_generate
import stt
import briefings
import leds
from ai_response import AIResponder
from ai_local import LocalAIResponder
from conversation_log import SessionLogger
from responder import Responder
from handlers.clip_handler import RealClipHandler
from handlers.timer_alert import TimerAlertRunner
from handler_base import load_clips_from_index, ResponseStream
from logger import get_logger
from config import cfg
from metrics import metrics

log = get_logger("converse")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

KEYWORD_PATH    = os.path.join(SCRIPT_DIR, "hey-bender.ppn")
SILENCE_TIMEOUT = 8.0   # seconds of silence before session ends


def _write_session_file(session_id: str, turns: int):
    try:
        with open(cfg.session_file, "w") as f:
            json.dump({
                "active": True,
                "session_id": session_id,
                "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "turns": turns,
            }, f)
    except OSError as e:
        log.warning("Failed to write session file: %s", e)


def _remove_session_file():
    for p in [cfg.session_file, cfg.end_session_file]:
        try:
            if os.path.exists(p):
                os.unlink(p)
        except OSError:
            pass


def _cleanup_abort_files():
    """Remove abort and end-session IPC files."""
    for f in [cfg.end_session_file, cfg.abort_file]:
        try:
            os.unlink(f)
        except OSError:
            pass

# ---------------------------------------------------------------------------
# Thinking clips
# ---------------------------------------------------------------------------

_thinking_clips = []


def _load_thinking_clips():
    global _thinking_clips
    _idx = os.path.join(BASE_DIR, "speech", "responses", "index.json")
    _thinking_clips = load_clips_from_index("thinking", _idx, BASE_DIR)


# ---------------------------------------------------------------------------
# Timer alert runner
# ---------------------------------------------------------------------------

_alert_runner = TimerAlertRunner()


# ---------------------------------------------------------------------------
# Greeting handler (used outside the responder chain)
# ---------------------------------------------------------------------------

_greeting_handler = RealClipHandler()

_last_abort_check = 0.0


def _check_abort_on_chunk(level):
    """LED visualisation callback + throttled abort file check (~10 Hz)."""
    global _last_abort_check
    leds.set_level(level)
    now = time.monotonic()
    if now - _last_abort_check > 0.1:
        _last_abort_check = now
        if os.path.exists(cfg.abort_file):
            audio.abort()


# ---------------------------------------------------------------------------
# Wake word detection
# ---------------------------------------------------------------------------

def wait_for_wakeword():
    porcupine = pvporcupine.create(
        access_key=os.environ["PORCUPINE_ACCESS_KEY"],
        keyword_paths=[KEYWORD_PATH],
    )
    # Use the shared PyAudio instance — creating a second one crashes PortAudio
    pa = audio.get_pa()
    stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
    )
    log.info("Listening for 'Hey Bender'...")
    try:
        while True:
            pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm_unpacked = struct.unpack_from("h" * porcupine.frame_length, pcm)
            if porcupine.process(pcm_unpacked) >= 0:
                log.info("Wake word detected")
                return
    finally:
        stream.stop_stream()
        stream.close()
        porcupine.delete()


# ---------------------------------------------------------------------------
# Conversation session
# ---------------------------------------------------------------------------

def run_session(ai: AIResponder, session_log: SessionLogger, responder: Responder, ai_local=None):
    metrics.count("session", event="start")
    session_log.session_start()
    _write_session_file(session_log.session_id, 0)
    audio.open_session()

    # Greeting — skip audio if silent_wakeword is enabled (LED-only notification)
    if cfg.silent_wakeword and cfg.led_listening_enabled:
        log.info("Silent wake word mode — skipping audio greeting")
        session_log.log_turn("(wake word)", "GREETING", None, "silent",
                     response_text="(silent — LED only)")
    else:
        greeting_resp = _greeting_handler.handle("(wake word)", "GREETING")
        if greeting_resp:
            leds.set_talking()
            audio.play(greeting_resp.wav_path, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
            session_log.log_turn("(wake word)", "GREETING", None, greeting_resp.method,
                         response_text=os.path.basename(greeting_resp.wav_path))
        else:
            text = "Yo. What do you want?"
            wav = tts_generate.speak(text)
            try:
                leds.set_talking()
                audio.play(wav, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
            finally:
                os.unlink(wav)
            session_log.log_turn("(wake word)", "GREETING", None, "pre_gen_tts", response_text=text)

    ai.clear_history()
    last_heard = time.time()

    while True:
        # Check for remote end-session request
        if os.path.exists(cfg.end_session_file):
            _cleanup_abort_files()
            log.info("Remote end-session: abrupt exit")
            leds.all_off()
            session_log.session_end("remote_abrupt")
            metrics.count("session", event="end", turns=session_log.turn, reason="remote_abrupt")
            _remove_session_file()
            audio.close_session()
            if ai_local:
                ai_local.clear_history()
            return

        # Show listening LEDs
        leds.set_listening(True)

        # Listen
        text = stt.listen_and_transcribe()

        if not text:
            if time.time() - last_heard > SILENCE_TIMEOUT:
                log.info("Silence timeout -- ending session")
                leds.all_off()
                session_log.session_end("timeout")
                metrics.count("session", event="end", turns=session_log.turn, reason="timeout")
                _remove_session_file()
                audio.close_session()
                if ai_local:
                    ai_local.clear_history()
                return
            continue

        last_heard = time.time()
        log.info("Heard: %r", text)
        _turn_start = time.monotonic()

        response = responder.get_response(text, ai, ai_local=ai_local)

        # DISMISSAL fast-path — skip farewell clip if configured
        if response.intent == "DISMISSAL" and cfg.dismissal_ends_session:
            log.info("DISMISSAL: abrupt session end")
            if not isinstance(response, ResponseStream) and response.is_temp and response.wav_path is not None:
                try:
                    os.unlink(response.wav_path)
                except OSError:
                    pass
            leds.all_off()
            session_log.log_turn(text, "DISMISSAL", response.sub_key,
                            "abrupt_stop", response.text)
            session_log.session_end("dismissal_abrupt")
            metrics.count("session", event="end", turns=session_log.turn, reason="dismissal_abrupt")
            _remove_session_file()
            audio.close_session()
            if ai_local:
                ai_local.clear_history()
            return

        # Play response — three cases: streaming cloud AI, sync AI, pre-generated clip
        if isinstance(response, ResponseStream):
            # Streaming cloud AI: LLM tokens → TTS → play concurrently
            _collected: list[str] = []

            def _collecting_iter(it):
                for s in it:
                    _collected.append(s)
                    yield s

            leds.set_talking()
            audio.play_stream(
                tts_generate.speak_from_iter(_collecting_iter(response.sentence_iter)),
                on_chunk=_check_abort_on_chunk,
                on_done=leds.all_off,
            )
            _response_text = " ".join(_collected)
            _response_model = response.model
            _response_method = response.method
            _response_routing = response.routing_log

        elif response.wav_path is None:
            # Local/synchronous AI response (Phase 2 path)
            leds.set_talking()
            audio.play_stream(
                tts_generate.speak_streaming(response.text),
                on_chunk=_check_abort_on_chunk,
                on_done=leds.all_off,
            )
            _response_text = response.text
            _response_model = response.model
            _response_method = response.method
            _response_routing = response.routing_log

        else:
            # Pre-generated clip or handler response
            if response.needs_thinking and cfg.thinking_sound and _thinking_clips:
                audio.play(random.choice(_thinking_clips), on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
            leds.set_talking()
            audio.play(response.wav_path, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
            _response_text = response.text
            _response_model = getattr(response, 'model', None)
            _response_method = response.method
            _response_routing = getattr(response, 'routing_log', None)

        metrics._write({"type": "timer", "name": "turn_total",
                        "duration_ms": round((time.monotonic() - _turn_start) * 1000, 1),
                        "intent": response.intent, "method": _response_method})

        # Check if playback was aborted (UI stop button pressed during response)
        if audio.was_aborted() or os.path.exists(cfg.end_session_file):
            _cleanup_abort_files()
            log.info("Session aborted during playback")
            if not isinstance(response, ResponseStream) and response.is_temp and response.wav_path is not None:
                try:
                    os.unlink(response.wav_path)
                except OSError:
                    pass
            leds.all_off()
            session_log.session_end("aborted")
            metrics.count("session", event="end", turns=session_log.turn, reason="aborted")
            _remove_session_file()
            audio.close_session()
            if ai_local:
                ai_local.clear_history()
            return

        if not isinstance(response, ResponseStream) and response.is_temp and response.wav_path is not None:
            try:
                os.unlink(response.wav_path)
            except OSError:
                pass

        session_log.log_turn(text, response.intent, response.sub_key,
                        _response_method, _response_text, _response_model,
                        ai_routing=_response_routing)
        _write_session_file(session_log.session_id, session_log.turn)

        if response.intent == "DISMISSAL":
            if cfg.dismissal_ends_session:
                # Should have been caught by fast-path above, but handle as fallback
                leds.all_off()
                session_log.session_end("dismissal")
                metrics.count("session", event="end", turns=session_log.turn, reason="dismissal")
                _remove_session_file()
                audio.close_session()
                if ai_local:
                    ai_local.clear_history()
                return
            else:
                # Soft stop — session continues
                log.info("DISMISSAL: soft stop, session continues")
                session_log.log_turn(text, "DISMISSAL", response.sub_key,
                                "soft_stop", response.text)
                last_heard = time.time()
                continue

        # Reset timer after Bender finishes -- gives user full window to respond
        last_heard = time.time()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    ai = AIResponder()
    ai_local = None
    if cfg.ai_backend != "cloud_only":
        try:
            ai_local = LocalAIResponder()
            log.info("Local AI responder initialised (model: %s at %s)",
                     cfg.local_llm_model, cfg.local_llm_url)
        except Exception as e:
            log.warning("Local AI init failed: %s — cloud-only mode", e)
    responder = Responder()
    import threading
    threading.Thread(target=briefings.refresh_all, daemon=True, name="briefings-refresh").start()
    # Warm up Piper TTS (pre-loads ONNX model)
    tts_generate.warm_up()
    # Load thinking clips from index
    _load_thinking_clips()
    # Clean up stale IPC files from previous crashes
    _cleanup_abort_files()
    _remove_session_file()
    log.info("Listening for 'Hey Bender'...")
    while True:
        try:
            # Check for fired timers before listening for wake word
            import timers as timers_mod
            fired = timers_mod.check_fired()
            if fired:
                _alert_runner.run(fired, on_chunk=_check_abort_on_chunk,
                                  on_done=leds.all_off,
                                  on_flash=leds.set_alert_flash)
                continue

            wait_for_wakeword()
            session_log = SessionLogger()
            run_session(ai, session_log, responder, ai_local)
        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except Exception as e:
            log.error("Error: %s", e)
            time.sleep(2)


if __name__ == "__main__":
    main()
