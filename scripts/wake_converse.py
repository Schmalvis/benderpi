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
    audio.open_session()

    # Greeting
    greeting_path = responder.pick_clip("GREETING")
    if greeting_path and os.path.exists(greeting_path):
        audio.play(greeting_path)
        method = "pre_gen_tts" if responder._is_pre_gen(greeting_path) else "real_clip"
        session_log.log_turn("(wake word)", "GREETING", None, method,
                     response_text=os.path.basename(greeting_path))
    else:
        text = "Yo. What do you want?"
        wav = tts_generate.speak(text)
        try:
            audio.play(wav)
        finally:
            os.unlink(wav)
        session_log.log_turn("(wake word)", "GREETING", None, "pre_gen_tts", response_text=text)

    ai.clear_history()
    last_heard = time.time()

    while True:
        # Listen
        text = stt.listen_and_transcribe()

        if not text:
            if time.time() - last_heard > SILENCE_TIMEOUT:
                log.info("Silence timeout -- ending session")
                session_log.session_end("timeout")
                metrics.count("session", event="end", turns=session_log.turn, reason="timeout")
                audio.close_session()
                return
            continue

        last_heard = time.time()
        log.info("Heard: %r", text)

        response = responder.get_response(text, ai)

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

        if response.intent == "DISMISSAL":
            session_log.session_end("dismissal")
            metrics.count("session", event="end", turns=session_log.turn, reason="dismissal")
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
    log.info("Listening for 'Hey Bender'...")
    while True:
        try:
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
