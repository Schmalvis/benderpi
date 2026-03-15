#!/usr/bin/env python3
"""
Bender conversational mode -- full conversation loop with logging.

Flow:
  1. Wait for "Hey Bender" wake word
  2. Play greeting clip
  3. Listen -> STT -> intent -> respond
     Priority: real_clip -> pre_gen_tts -> promoted_tts -> handler -> ai_fallback
     WEATHER and NEWS use cached briefing WAVs (not temp files -- do not unlink)
  4. Log every turn to logs/YYYY-MM-DD.jsonl
  5. Loop until silence timeout or DISMISSAL
"""

import os
import random
import json
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
import intent as intent_mod
import briefings
from ai_response import AIResponder, MODEL
from handlers import ha_control
from conversation_log import SessionLogger
from logger import get_logger

log = get_logger("converse")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INDEX_PATH      = os.path.join(BASE_DIR, "speech", "responses", "index.json")
KEYWORD_PATH    = os.path.join(SCRIPT_DIR, "hey-bender.ppn")
SILENCE_TIMEOUT = 8.0   # seconds of silence before session ends

# ---------------------------------------------------------------------------
# Response library
# ---------------------------------------------------------------------------

with open(INDEX_PATH) as f:
    INDEX = json.load(f)


def _full_path(relative: str) -> str:
    return os.path.join(BASE_DIR, relative)


def pick_clip(intent_name: str, sub_key: str = None) -> str | None:
    if intent_name == "PERSONAL":
        path = INDEX.get("personal", {}).get(sub_key)
        return _full_path(path) if path else None
    clips = INDEX.get(intent_name.lower())
    if not clips or not isinstance(clips, list):
        return None
    return _full_path(random.choice(clips))


def _is_pre_gen(path: str) -> bool:
    """True if the clip is from speech/responses/ (not speech/wav/)."""
    return "speech/responses" in path.replace(BASE_DIR, "")


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

def run_session(ai: AIResponder, log: SessionLogger):
    log.session_start()
    audio.open_session()

    # Greeting
    greeting_path = pick_clip("GREETING")
    if greeting_path and os.path.exists(greeting_path):
        audio.play(greeting_path)
        method = "pre_gen_tts" if _is_pre_gen(greeting_path) else "real_clip"
        log.log_turn("(wake word)", "GREETING", None, method,
                     response_text=os.path.basename(greeting_path))
    else:
        text = "Yo. What do you want?"
        wav = tts_generate.speak(text)
        audio.play(wav)
        os.unlink(wav)
        log.log_turn("(wake word)", "GREETING", None, "pre_gen_tts", response_text=text)

    ai.clear_history()
    last_heard = time.time()

    while True:
        # Listen
        text = stt.listen_and_transcribe()

        if not text:
            if time.time() - last_heard > SILENCE_TIMEOUT:
                log.info("Silence timeout -- ending session")
                log.session_end("timeout")
                audio.close_session()
                return
            continue

        last_heard = time.time()
        log.info("Heard: %r", text)

        # Classify
        intent_name, sub_key = intent_mod.classify(text)
        log.info("Intent: %s%s", intent_name, f" / {sub_key}" if sub_key else "")

        # --- DISMISSAL ---
        if intent_name == "DISMISSAL":
            clip = pick_clip("DISMISSAL")
            if clip and os.path.exists(clip):
                audio.play(clip)
                method = "pre_gen_tts" if _is_pre_gen(clip) else "real_clip"
                log.log_turn(text, intent_name, None, method,
                             response_text=os.path.basename(clip))
            else:
                reply = "Yeah yeah, see ya."
                wav = tts_generate.speak(reply)
                audio.play(wav)
                os.unlink(wav)
                log.log_turn(text, intent_name, None, "pre_gen_tts", response_text=reply)
            log.session_end("dismissal")
            audio.close_session()
            return

        # --- PROMOTED (AI query promoted to static clip) ---
        elif intent_name == "PROMOTED":
            clip_path = _full_path(sub_key)  # sub_key holds the file path
            if os.path.exists(clip_path):
                audio.play(clip_path)
                log.log_turn(text, "PROMOTED", None, "promoted_tts",
                             response_text=os.path.basename(clip_path))
            else:
                # File missing -- fall through to AI
                _respond_ai(text, ai, log)

        # --- GREETING / AFFIRMATION / JOKE / PERSONAL (pre-gen or real clip) ---
        elif intent_name in ("GREETING", "AFFIRMATION", "JOKE", "PERSONAL"):
            clip = pick_clip(intent_name, sub_key)
            if clip and os.path.exists(clip):
                audio.play(clip)
                method = "pre_gen_tts" if _is_pre_gen(clip) else "real_clip"
                log.log_turn(text, intent_name, sub_key, method,
                             response_text=os.path.basename(clip))
            else:
                _respond_ai(text, ai, log, intent_name, sub_key)

        # --- WEATHER ---
        elif intent_name == "WEATHER":
            try:
                wav = briefings.get_weather_wav()
                audio.play(wav)
                log.log_turn(text, intent_name, None, "handler_weather")
            except Exception as e:
                log.error("Weather handler error: %s", e)
                _respond_ai(text, ai, log, intent_name)

        # --- NEWS ---
        elif intent_name == "NEWS":
            try:
                wav = briefings.get_news_wav()
                audio.play(wav)
                log.log_turn(text, intent_name, None, "handler_news")
            except Exception as e:
                log.error("News handler error: %s", e)
                _respond_ai(text, ai, log, intent_name)

        # --- HA_CONTROL ---
        elif intent_name == "HA_CONTROL":
            try:
                wav = ha_control.control(text)
                audio.play(wav)
                os.unlink(wav)
                log.log_turn(text, intent_name, None, "handler_ha")
            except Exception as e:
                log.error("HA control error: %s", e)
                _respond_ai(text, ai, log, intent_name)

        # --- UNKNOWN -> AI ---
        else:
            _respond_ai(text, ai, log)

        # Reset timer after Bender finishes -- gives user full window to respond
        last_heard = time.time()


def _respond_ai(user_text: str, ai: AIResponder, log: SessionLogger,
                intent_name: str = "UNKNOWN", sub_key: str = None):
    """Call AI fallback, play response, log the turn."""
    try:
        wav = ai.respond(user_text)
        # Grab last assistant reply for the log
        reply = ai.history[-1]["content"] if ai.history else ""
        audio.play(wav)
        os.unlink(wav)
        log.log_turn(user_text, intent_name, sub_key, "ai_fallback",
                     response_text=reply, model=MODEL)
    except Exception as e:
        error_text = f"Something went very wrong. Error: {type(e).__name__}."
        wav = tts_generate.speak(error_text)
        audio.play(wav)
        os.unlink(wav)
        log.log_turn(user_text, intent_name, sub_key, "error_fallback",
                     response_text=str(e))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    ai = AIResponder()
    import threading
    threading.Thread(target=briefings.refresh_all, daemon=True, name="briefings-refresh").start()
    log.info("Listening for 'Hey Bender'...")
    while True:
        try:
            wait_for_wakeword()
            session_log = SessionLogger()
            run_session(ai, session_log)
        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except Exception as e:
            log.error("Error: %s", e)
            time.sleep(2)


if __name__ == "__main__":
    main()
