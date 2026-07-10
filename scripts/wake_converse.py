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
import threading
from collections import deque

import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR   = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

# Load .env
from dotenv import dotenv_values
_env = dotenv_values(os.path.join(BASE_DIR, ".env"))
os.environ.update({k: v for k, v in _env.items() if v})

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

OWW_FRAME_SIZE = 1280


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


def _load_oww_model(model_path: str):
    if not os.path.isfile(model_path):
        log.error(
            "Wake word model not found at %s (oww_model_path in bender_config.json). "
            "openWakeWord will NOT silently fall back to a bundled model — the "
            "wake loop cannot start. Run `bash scripts/deploy_hey_bender.sh` on "
            "the Pi to download it, or fix oww_model_path. Exiting.",
            model_path,
        )
        sys.exit(1)
    from openwakeword.model import Model
    return Model(wakeword_model_paths=[model_path])


def _open_wake_stream():
    """Open the 16kHz mic input stream for wake-word listening.

    Returns (stream, capture_channels, device_name).
    """
    pa = audio.get_pa()
    input_device_index = audio.get_input_device_index()
    device_name = (
        pa.get_device_info_by_index(input_device_index)["name"]
        if input_device_index is not None else ""
    )
    capture_channels = 2 if "xvf_dsnoop" in device_name else 1
    stream = pa.open(
        rate=16000,
        channels=capture_channels,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=OWW_FRAME_SIZE,
        input_device_index=input_device_index,
    )
    return stream, capture_channels, device_name


def _feed_watchdog():
    """Send systemd WATCHDOG=1 (no-op if systemd unavailable)."""
    try:
        from systemd import daemon as _sd_daemon
        _sd_daemon.notify("WATCHDOG=1")
    except Exception:
        pass


def wait_for_wakeword(_oww_model=None):
    oww_model = _oww_model or _load_oww_model(
        os.path.join(BASE_DIR, cfg.oww_model_path)
    )

    stall_s = float(cfg.wake_stall_seconds)
    hb_every = int(cfg.wake_heartbeat_frames)
    read_timeout_s = float(getattr(cfg, "mic_read_timeout_s", 10.0))
    max_reinits = int(getattr(cfg, "mic_stall_max_reinits", 1))

    # N-of-M temporal smoothing config. window >= 1, required clamped to window.
    window = max(1, int(getattr(cfg, "oww_window", 1)))
    required = max(1, min(int(getattr(cfg, "oww_frames_required", 1)), window))

    # Input-sanity (RMS sentinel + periodic score/RMS logging) config.
    rms_floor = float(getattr(cfg, "wake_rms_floor", 0.0))
    silence_alarm_s = float(getattr(cfg, "wake_silence_alarm_s", 0.0))
    score_log_interval_s = float(getattr(cfg, "wake_score_log_interval_s", 0.0))

    reinit_count = 0
    while True:
        stream, capture_channels, device_name = _open_wake_stream()
        reader = audio.MicReader(
            stream, OWW_FRAME_SIZE, read_timeout_s, name="wake-mic-reader"
        )
        log.info("Listening for wake word... (mic: %s, ch=%d, model: %s, "
                 "threshold: %.2f, smoothing: %d-of-%d)",
                 device_name or "default", capture_channels,
                 os.path.basename(cfg.oww_model_path), cfg.oww_threshold,
                 required, window)

        last_read_ts = time.monotonic()
        frames_since_hb = 0
        # Smoothing + sentinel state is per-stream: reset on every (re)open so a
        # stale score/level from before a session or reinit can never trigger.
        recent_hits = deque(maxlen=window)  # 1.0 if frame >= threshold else 0.0
        last_rms_ok_ts = time.monotonic()   # last time input level cleared floor
        last_score_log_ts = time.monotonic()
        window_max_score = 0.0              # peak score since last periodic log
        window_max_rms = 0.0                # peak input RMS since last periodic log
        try:
            while True:
                try:
                    pcm = reader.read(read_timeout_s)
                except audio.MicStallError as exc:
                    # No frame at all within the timeout — the blocking read is
                    # wedged. Feed the watchdog so systemd doesn't kill us mid
                    # diagnosis, then escalate (reinit once, then exit).
                    _feed_watchdog()
                    log.error("Wake loop stalled (no mic frame in %.1fs): %s",
                              read_timeout_s, exc)
                    raise RuntimeError("wake loop stalled") from exc

                now = time.monotonic()
                if not pcm or len(pcm) == 0:
                    # Zero-length reads still arrive as frames; apply the classic
                    # no-PCM stall accounting. Keep feeding the watchdog + emit
                    # heartbeats so we stay alive while diagnostics accrue.
                    if now - last_read_ts > stall_s:
                        log.error("Wake loop stalled: %.1fs of zero-length PCM frames",
                                  now - last_read_ts)
                        raise RuntimeError("wake loop stalled")
                    frames_since_hb += 1
                    if frames_since_hb >= hb_every:
                        metrics.count("wake_loop_heartbeat")
                        _feed_watchdog()
                        frames_since_hb = 0
                    continue
                last_read_ts = now
                frames_since_hb += 1
                if frames_since_hb >= hb_every:
                    metrics.count("wake_loop_heartbeat")
                    _feed_watchdog()
                    frames_since_hb = 0

                # Input-level sanity: a mic feeding zeros/garbage still delivers
                # frames (so the stall detector above never fires) but can never
                # trigger the wake word — the 6-day XVF3800 failure. Track the
                # rolling RMS; if it stays below the floor for the alarm window,
                # escalate through the same reinit-then-exit path as a hard stall.
                frame_rms = audio.rms(pcm, 2)
                if frame_rms > window_max_rms:
                    window_max_rms = frame_rms
                if rms_floor <= 0.0 or frame_rms >= rms_floor:
                    last_rms_ok_ts = now
                elif silence_alarm_s > 0.0 and now - last_rms_ok_ts > silence_alarm_s:
                    metrics.count("wake_mic_silent",
                                  silent_s=round(now - last_rms_ok_ts, 1),
                                  floor=rms_floor)
                    log.error("Wake mic silent: input RMS below %.0f for %.1fs "
                              "(presumed dead/garbage mic feed)",
                              rms_floor, now - last_rms_ok_ts)
                    raise RuntimeError("wake loop stalled")

                pcm_np = np.frombuffer(pcm, dtype=np.int16)
                if capture_channels == 2:
                    pcm_np = pcm_np[::2]  # left channel only (stereo downmix)
                try:
                    prediction = oww_model.predict(pcm_np)
                except Exception as exc:  # a bad frame must not kill the loop
                    log.warning("oww predict() failed on a frame: %s", exc)
                    metrics.count("wake_predict_error")
                    recent_hits.append(0.0)
                    continue

                frame_score = max(prediction.values()) if prediction else 0.0
                if frame_score > window_max_score:
                    window_max_score = frame_score

                # Periodic max-score + RMS line so journald distinguishes
                # "nobody said the wake word" (scores near 0, RMS healthy) from
                # "mic feeding garbage" (RMS near 0) without a live console.
                if (score_log_interval_s > 0.0
                        and now - last_score_log_ts >= score_log_interval_s):
                    log.info("Wake idle: peak score %.3f, peak RMS %.0f over last %.0fs",
                             window_max_score, window_max_rms,
                             now - last_score_log_ts)
                    last_score_log_ts = now
                    window_max_score = 0.0
                    window_max_rms = 0.0

                # N-of-M temporal smoothing: require multiple recent frames over
                # threshold before firing, suppressing single-frame spikes.
                recent_hits.append(1.0 if frame_score >= cfg.oww_threshold else 0.0)
                if sum(recent_hits) >= required:
                    log.info("Wake word detected (score: %.3f, %d/%d frames over "
                             "threshold)", frame_score, int(sum(recent_hits)), window)
                    return  # reader stopped in finally
        except RuntimeError as exc:
            if "stalled" not in str(exc):
                raise
            # Escalation: honest failure beats fake self-healing. The 2026 USB
            # wedge needed a physical reseat — an infinite reinit loop against a
            # dead device just spins. Reinit once in-process; if it stalls again,
            # exit loudly and let systemd's Restart=on-failure take over.
            metrics.count("wake_loop_stall_reinit", reinit_count=reinit_count)
            if reinit_count >= max_reinits:
                log.error("Wake mic stalled again after %d reinit(s) — exiting for "
                          "systemd restart (device likely needs physical reseat)",
                          reinit_count)
                metrics.count("wake_loop_stall_exit")
                sys.exit(1)
            reinit_count += 1
            log.warning("Reinitialising wake mic (attempt %d/%d) after stall",
                        reinit_count, max_reinits)
            time.sleep(1.0)
            # loop back to re-open the stream
        finally:
            reader.stop()




# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    for _secret in cfg.validate():
        metrics.count("secrets_missing", secret=_secret)
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
    # Startup mic self-test — WARNs loudly if the mic path looks wedged/silent,
    # but never blocks startup (the mic may re-enumerate and recover).
    try:
        audio.mic_selftest()
    except Exception as exc:
        log.warning("Mic self-test raised unexpectedly: %s — continuing", exc)
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
                rec_start = time.time()
                text = stt.listen_and_transcribe()
                if not text:
                    # Anchor the silence timeout on when this recording STARTED, not on
                    # `now`. A slow CPU-whisper transcription can stall 20-110s on
                    # silence/noise and then return empty; measuring `now - last_heard`
                    # would make the timeout trivially true and end the session the
                    # instant the slow transcription returns — the user never actually
                    # got an idle window, they just waited out a stuck transcription.
                    # Using rec_start means a slow (empty) transcription instead grants
                    # another live recording attempt, while genuine silence still
                    # accumulates recording time across cycles and ends the session.
                    if rec_start - last_heard > cfg.silence_timeout:
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
