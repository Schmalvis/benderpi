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
import re
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
from conversation_log import SessionLogger
from responder import Responder
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

# ---------------------------------------------------------------------------
# Thinking clips
# ---------------------------------------------------------------------------

_thinking_clips = []


def _load_thinking_clips():
    global _thinking_clips
    index_path = os.path.join(BASE_DIR, "speech", "responses", "index.json")
    try:
        with open(index_path) as f:
            index = json.load(f)
        _thinking_clips = [
            os.path.join(BASE_DIR, p)
            for p in index.get("thinking", [])
            if os.path.exists(os.path.join(BASE_DIR, p))
        ]
        log.info("Loaded %d thinking clip(s)", len(_thinking_clips))
    except Exception as e:
        log.warning("Could not load thinking clips: %s", e)
        _thinking_clips = []


# ---------------------------------------------------------------------------
# Timer alert clips
# ---------------------------------------------------------------------------

_timer_alert_clips = []


def _load_timer_alert_clips():
    global _timer_alert_clips
    index_path = os.path.join(BASE_DIR, "speech", "responses", "index.json")
    try:
        with open(index_path) as f:
            index = json.load(f)
        _timer_alert_clips = [
            os.path.join(BASE_DIR, p)
            for p in index.get("timer_alerts", [])
            if os.path.exists(os.path.join(BASE_DIR, p))
        ]
        log.info("Loaded %d timer alert clip(s)", len(_timer_alert_clips))
    except Exception as e:
        log.warning("Could not load timer alert clips: %s", e)
        _timer_alert_clips = []


# ---------------------------------------------------------------------------
# Timer dismiss detection
# ---------------------------------------------------------------------------

TIMER_DISMISS_PATTERNS = [
    r"\b(stop|enough|ok|okay|shut up|quiet|silence|dismiss)\b",
    r"\bthat'?s?\s*(enough|ok|fine)\b",
    r"\bplease stop\b",
    r"\byes\b",
    r"\bgot it\b",
    r"\bthank(s| you)\b",
]


def _is_timer_dismiss(text: str) -> bool:
    t = text.strip().lower()
    return any(re.search(p, t, re.IGNORECASE) for p in TIMER_DISMISS_PATTERNS)


# ---------------------------------------------------------------------------
# Timer alert mode
# ---------------------------------------------------------------------------

def run_timer_alert(fired_timers: list):
    """Play-pause alert cycle for fired timers until dismissed."""
    import timers as timers_mod

    labels = [t["label"] for t in fired_timers]
    label_str = ", ".join(labels) if labels else "timer"
    log.info("Timer alert: %s", label_str)
    metrics.count("timer_alert", labels=label_str)

    max_seconds = cfg.timer_alert_max_seconds
    start_time = time.time()
    dismissed_by_voice = False

    # Start LED alert flash
    leds.set_alert_flash(True)

    while time.time() - start_time < max_seconds:
        # 1. Play an alert clip
        audio.open_session()
        if _timer_alert_clips:
            clip = random.choice(_timer_alert_clips)
            audio.play(clip)
        else:
            # Fallback: generate TTS
            wav = tts_generate.speak(f"Timer for {label_str} is done!")
            audio.play(wav)
            try:
                os.unlink(wav)
            except OSError:
                pass
        audio.close_session()

        # 2. Listen for dismissal (~3 seconds)
        text = stt.listen_and_transcribe()
        if text and _is_timer_dismiss(text):
            log.info("Timer dismissed by voice: %r", text)
            dismissed_by_voice = True
            break

        # Also check web UI dismissal (file-based)
        remaining_fired = timers_mod.check_fired()
        if not remaining_fired:
            log.info("Timer dismissed via UI")
            break

    # Stop LED flash
    leds.set_alert_flash(False)
    leds.all_off()

    # Dismiss all fired timers
    count = timers_mod.dismiss_all_fired()
    log.info("Dismissed %d timer(s)", count)
    metrics.count("timer_dismissed", count=count,
                  method="voice" if dismissed_by_voice else "timeout")

    # Play dismissal confirmation
    audio.open_session()
    responses = [
        f"Finally. {label_str} timer dismissed.",
        f"About time. {label_str} done and dismissed.",
        "Dismissed. You're welcome. Again.",
    ]
    wav = tts_generate.speak(random.choice(responses))
    audio.play(wav)
    try:
        os.unlink(wav)
    except OSError:
        pass
    audio.close_session()


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

def run_session(ai: AIResponder, session_log: SessionLogger, responder: Responder):
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
        greeting_path = responder.pick_clip("GREETING")
        if greeting_path and os.path.exists(greeting_path):
            leds.set_talking()
            audio.play(greeting_path)
            method = "pre_gen_tts" if responder._is_pre_gen(greeting_path) else "real_clip"
            session_log.log_turn("(wake word)", "GREETING", None, method,
                         response_text=os.path.basename(greeting_path))
        else:
            text = "Yo. What do you want?"
            wav = tts_generate.speak(text)
            try:
                leds.set_talking()
                audio.play(wav)
            finally:
                os.unlink(wav)
            session_log.log_turn("(wake word)", "GREETING", None, "pre_gen_tts", response_text=text)

    ai.clear_history()
    last_heard = time.time()

    while True:
        # Check for remote end-session request
        if os.path.exists(cfg.end_session_file):
            try:
                os.unlink(cfg.end_session_file)
            except OSError:
                pass
            log.info("Session ended by remote request")
            if not (cfg.silent_wakeword and cfg.led_listening_enabled):
                clip = responder.pick_clip("DISMISSAL")
                if clip and os.path.exists(clip):
                    leds.set_talking()
                    audio.play(clip)
            leds.all_off()
            session_log.session_end("remote_end")
            metrics.count("session", event="end", turns=session_log.turn, reason="remote_end")
            _remove_session_file()
            audio.close_session()
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
                return
            continue

        last_heard = time.time()
        log.info("Heard: %r", text)

        response = responder.get_response(text, ai)

        # Switch to talking LEDs
        leds.set_talking()

        # Play thinking sound while slow response is being generated
        if response.needs_thinking and cfg.thinking_sound and _thinking_clips:
            audio.play(random.choice(_thinking_clips))

        # Play response
        audio.play(response.wav_path)
        if response.is_temp:
            try:
                os.unlink(response.wav_path)
            except OSError:
                pass

        session_log.log_turn(text, response.intent, response.sub_key,
                        response.method, response.text, response.model)
        _write_session_file(session_log.session_id, session_log.turn)

        if response.intent == "DISMISSAL":
            leds.all_off()
            session_log.session_end("dismissal")
            metrics.count("session", event="end", turns=session_log.turn, reason="dismissal")
            _remove_session_file()
            audio.close_session()
            return

        # Reset timer after Bender finishes -- gives user full window to respond
        last_heard = time.time()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    ai = AIResponder()
    responder = Responder()
    import threading
    threading.Thread(target=briefings.refresh_all, daemon=True, name="briefings-refresh").start()
    # Warm up Piper TTS (pre-loads ONNX model)
    tts_generate.warm_up()
    # Load thinking clips from index
    _load_thinking_clips()
    # Load timer alert clips from index
    _load_timer_alert_clips()
    log.info("Listening for 'Hey Bender'...")
    while True:
        try:
            # Check for fired timers before listening for wake word
            import timers as timers_mod
            fired = timers_mod.check_fired()
            if fired:
                run_timer_alert(fired)
                continue

            wait_for_wakeword()
            session_log = SessionLogger()
            run_session(ai, session_log, responder)
        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except Exception as e:
            log.error("Error: %s", e)
            time.sleep(2)


if __name__ == "__main__":
    main()
