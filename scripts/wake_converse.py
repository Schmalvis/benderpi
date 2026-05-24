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

import atexit
import os
import signal
import time
import sys
import struct
import threading

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
import vision
from ai_response import AIResponder
from ai_local import LocalAIResponder
from conversation_log import SessionLogger
from responder import Responder
from handlers.timer_alert import TimerAlertRunner
from logger import get_logger
from config import cfg
from metrics import metrics
from session import ConversationSession, FutureVisionProvider

log = get_logger("converse")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

KEYWORD_PATH = os.path.join(SCRIPT_DIR, "hey-bender.ppn")


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
# Timer alert runner
# ---------------------------------------------------------------------------

_alert_runner = TimerAlertRunner()

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
    input_device_index = audio.get_input_device_index()
    device_name = (
        pa.get_device_info_by_index(input_device_index)["name"]
        if input_device_index is not None else ""
    )
    # xvf_dsnoop (reSpeaker 4-mic) only supports stereo — downmix to mono for Porcupine
    capture_channels = 2 if "xvf_dsnoop" in device_name else 1
    stream = pa.open(
        rate=porcupine.sample_rate,
        channels=capture_channels,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
        input_device_index=input_device_index,
    )
    log.info("Listening for 'Hey Bender'... (mic: %s, ch=%d)", device_name or "default", capture_channels)

    stall_s = float(cfg.wake_stall_seconds)
    hb_every = int(cfg.wake_heartbeat_frames)
    last_read_ts = time.monotonic()
    frames_since_hb = 0

    try:
        while True:
            pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
            now = time.monotonic()
            if not pcm or len(pcm) == 0:
                if now - last_read_ts > stall_s:
                    log.error("Wake loop stalled: %.1fs since last PCM frame", now - last_read_ts)
                    raise RuntimeError("wake loop stalled")
                continue
            last_read_ts = now
            frames_since_hb += 1
            if frames_since_hb >= hb_every:
                metrics.count("wake_loop_heartbeat")
                try:
                    from systemd import daemon as _sd_daemon  # optional dep
                    _sd_daemon.notify("WATCHDOG=1")
                except Exception:
                    pass
                frames_since_hb = 0

            if capture_channels == 2:
                all_samples = struct.unpack_from("h" * porcupine.frame_length * 2, pcm)
                pcm_unpacked = all_samples[::2]  # left channel only
            else:
                pcm_unpacked = struct.unpack_from("h" * porcupine.frame_length, pcm)
            if porcupine.process(pcm_unpacked) >= 0:
                log.info("Wake word detected")
                return
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        porcupine.delete()




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
            threading.Thread(target=ai_local.warm_up, daemon=True, name="ollama-warmup").start()
            atexit.register(ai_local.close)
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        except Exception as e:
            log.warning("Local AI init failed: %s — cloud-only mode", e)
    responder = Responder()
    threading.Thread(target=briefings.refresh_all, daemon=True, name="briefings-refresh").start()
    # Warm up Piper TTS (pre-loads ONNX model)
    tts_generate.warm_up()
    # Pre-load STT model so first wake word has no init delay
    threading.Thread(target=stt.warm_up, daemon=True, name="stt-warmup").start()
    # Clean up stale IPC files from previous crashes
    _cleanup_abort_files()
    _remove_session_file()
    log.info("Listening for 'Hey Bender'...")
    try:
        from systemd import daemon as _sd_daemon
        _sd_daemon.notify("READY=1")
        log.info("systemd READY=1 sent (Type=notify)")
    except Exception as exc:
        log.debug("sd_notify not available: %s", exc)
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
            session = ConversationSession(
                ai=ai,
                ai_local=ai_local,
                responder=responder,
                session_log=SessionLogger(),
                vision=FutureVisionProvider() if cfg.vlm_enabled else None,
                on_audio_chunk=_check_abort_on_chunk,
            )
            session.start()
            last_heard = time.time()
            while True:
                leds.set_listening(True)
                text = stt.listen_and_transcribe()
                if not text:
                    if time.time() - last_heard > cfg.silence_timeout:
                        session.end("timeout")
                        break
                    continue
                last_heard = time.time()
                log.info("Heard: %r", text)
                stt.release()
                if ai_local:
                    ai_local.reset_hailo()
                result = session.handle_turn(text)
                if result.should_end:
                    session.end(result.end_reason or "end")
                    break
        except KeyboardInterrupt:
            log.info("Stopped.")
            break
        except RuntimeError as exc:
            if "stalled" in str(exc):
                log.error("Wake loop reinit after stall: %s", exc)
                metrics.count("wake_loop_stall_reinit")
                time.sleep(1.0)
                continue
            log.exception("Unhandled RuntimeError in wake/session loop — continuing")
            time.sleep(2.0)
        except Exception as e:
            log.error("Error: %s", e)
            time.sleep(2)


if __name__ == "__main__":
    main()
