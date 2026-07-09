#!/usr/bin/env python3
"""
Speech-to-text for Bender.

Backends (in priority order):
  1. Hailo Speech2Text (Whisper-Small on Hailo-10H NPU) — primary
  2. faster-whisper CPU (base.en) — fallback if Hailo unavailable

Usage (standalone test):
    python3 scripts/stt.py

Returns the transcribed text to stdout, or '' on timeout/silence.
"""

import os
import re
import wave
import tempfile
import collections
import threading
import time

import numpy as np
import webrtcvad

import audio as audio_mod
from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("stt")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SAMPLE_RATE    = 16000    # Hz — required by webrtcvad and whisper
CHANNELS       = 1
FRAME_MS       = 30       # VAD frame size in ms (10/20/30 supported)
FRAME_BYTES    = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 16-bit samples

# Hailo NPU backend (Whisper-Small HEF — primary)
WHISPER_HEF        = "/usr/local/hailo/resources/models/hailo10h/Whisper-Small.hef"

# Settle window (seconds) after releasing the Hailo VDevice before the LLM
# re-acquires the same SHARED group. NOT part of Hailo's one-shot reference —
# added defensively because VDMA/KV-cache teardown appears asynchronous and we
# re-acquire in the same long-running process. Set to 0 to disable.
_RELEASE_SETTLE_S = 0.15

WHISPER_HALLUCINATIONS = set(cfg.whisper_hallucinations)

# ---------------------------------------------------------------------------
# Backend init
# ---------------------------------------------------------------------------

_backend   = None   # "hailo" | "cpu"
_vdevice   = None   # Hailo VDevice (held open for lifetime of process)
_s2t       = None   # Hailo Speech2Text instance
_cpu_model = None   # faster-whisper fallback
_cpu_only_model = None  # dedicated CPU model for prefer_cpu callers (never Hailo)
_model_lock = threading.Lock()  # guards _backend/_vdevice/_s2t mutations


def _load_model():
    """Initialise Hailo Whisper-Small as primary STT, falling back to CPU on failure."""
    global _backend, _vdevice, _s2t, _cpu_model
    with _model_lock:
        if _backend is not None:
            return

        # Hailo primary
        if getattr(cfg, "hailo_stt_enabled", True) and os.path.exists(WHISPER_HEF):
            try:
                from hailo_platform import VDevice
                from hailo_platform.genai import Speech2Text, Speech2TextTask  # noqa: F401
                _params = VDevice.create_params()
                _params.group_id = "SHARED"
                # Construct without entering a context manager — Hailo's reference
                # (simple_whisper_chat.py) uses the objects directly after
                # construction and tears them down via .release(), never
                # __enter__/__exit__. Matching that avoids the __exit__ + __del__
                # double-release path.
                _vdevice = VDevice(_params)
                _s2t = Speech2Text(_vdevice, WHISPER_HEF)
                _backend = "hailo"
                log.info("STT backend: Hailo Speech2Text (Whisper-Small on Hailo-10H)")
                return
            except Exception as e:
                log.warning("Hailo STT init failed (%s) — falling back to CPU", e)
                # Release in reference order: Speech2Text before its VDevice.
                if _s2t is not None:
                    try:
                        _s2t.release()
                    except Exception:
                        pass
                    _s2t = None
                if _vdevice is not None:
                    try:
                        _vdevice.release()
                    except Exception:
                        pass
                    _vdevice = None

        # CPU fallback
        try:
            from faster_whisper import WhisperModel
            _cpu_model = WhisperModel(cfg.whisper_model, device="cpu", compute_type="int8", cpu_threads=3)
            _backend = "cpu"
            log.info("STT backend: faster-whisper CPU (%s)", cfg.whisper_model)
            return
        except Exception as e:
            log.warning("faster-whisper init failed (%s)", e)

        raise RuntimeError("No STT backend available")


def _active_model_name() -> str:
    return "whisper-small-hailo" if _backend == "hailo" else cfg.whisper_model


# ---------------------------------------------------------------------------
# Transcription helpers
# ---------------------------------------------------------------------------

def _transcribe_cpu(model, audio_array: np.ndarray) -> str:
    """Transcribe a float32 array with a faster-whisper CPU model.

    Uses faster-whisper's per-segment confidence signals to gate out
    hallucinated segments *before* they reach the phrase blocklist. This is the
    primary hallucination defence on the CPU path; the blocklist in
    ``_filter_hallucination`` stays as a backstop (and is the ONLY defence on
    the Hailo path, which returns text with no confidence signals).

    Gating (all thresholds config-overridable, permissive by default):
      * drop a segment only when it is BOTH probably-silence
        (``no_speech_prob > stt_no_speech_prob_max``) AND low-confidence
        (``avg_logprob < stt_avg_logprob_min``) — either alone keeps the segment
      * drop a segment whose ``compression_ratio > stt_compression_ratio_max``
        (repetitive garbage, e.g. "you you you you")

    Every rejection emits a ``stt_confidence_reject`` metric with the raw values
    so thresholds can be reviewed/tuned from logs/metrics.jsonl rather than by
    guesswork. Thresholds default to Whisper-lore canonical values and are
    deliberately permissive — over-gating drops quiet real speech, which feels
    worse than the occasional "thanks for watching".
    """
    no_speech_max = float(getattr(cfg, "stt_no_speech_prob_max", 0.6))
    logprob_min   = float(getattr(cfg, "stt_avg_logprob_min", -1.0))
    compress_max  = float(getattr(cfg, "stt_compression_ratio_max", 2.4))

    segments, _ = model.transcribe(
        audio_array,
        language="en",
        beam_size=1,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=True,
    )

    kept: list[str] = []
    for s in segments:
        text = (s.text or "").strip()
        if not text:
            continue
        no_speech = float(getattr(s, "no_speech_prob", 0.0) or 0.0)
        avg_logprob = float(getattr(s, "avg_logprob", 0.0) or 0.0)
        compression = float(getattr(s, "compression_ratio", 0.0) or 0.0)

        reason = ""
        if no_speech > no_speech_max and avg_logprob < logprob_min:
            reason = "low_confidence"
        elif compression > compress_max:
            reason = "repetition"

        if reason:
            log.warning(
                "STT segment rejected (%s): %r [no_speech=%.2f logprob=%.2f "
                "compression=%.2f]",
                reason, text[:60], no_speech, avg_logprob, compression,
            )
            metrics.count(
                "stt_confidence_reject",
                reason=reason,
                no_speech_prob=round(no_speech, 3),
                avg_logprob=round(avg_logprob, 3),
                compression_ratio=round(compression, 3),
                text=text[:80],
            )
            continue
        kept.append(text)

    return " ".join(kept).strip()


def _transcribe_array(audio_array: np.ndarray) -> str:
    """Transcribe a float32 numpy array. Assumes model already loaded."""
    if _backend == "hailo":
        from hailo_platform.genai import Speech2TextTask
        return _s2t.generate_all_text(
            audio_data=audio_array,
            task=Speech2TextTask.TRANSCRIBE,
            language="en",
        ).strip()
    else:
        return _transcribe_cpu(_cpu_model, audio_array)


def _load_cpu_only_model():
    """Load a dedicated CPU faster-whisper model that never touches the Hailo
    device. Used by non-latency-critical callers in *other* processes (the
    bender-web service) so a second process can't contend for the shared Hailo
    STT VDevice while bender-converse is orchestrating STT/LLM turn-taking."""
    global _cpu_only_model
    with _model_lock:
        if _cpu_only_model is not None:
            return _cpu_only_model
        from faster_whisper import WhisperModel
        _cpu_only_model = WhisperModel(
            cfg.whisper_model, device="cpu", compute_type="int8", cpu_threads=3
        )
        log.info("STT: CPU-only faster-whisper loaded (%s) — Hailo not touched",
                 cfg.whisper_model)
        return _cpu_only_model


def _wav_to_array(wav_path: str) -> np.ndarray:
    """Load a WAV file into a float32 numpy array normalised to [-1, 1]."""
    with wave.open(wav_path, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _filter_hallucination(text: str, source: str = "") -> str:
    # Catch repetitive-character garbage (ZZZZZZ, aaaaaaa, etc.)
    if re.search(r"(.)\1{5,}", text.lower().replace(" ", "")):
        log.warning("Whisper hallucination filtered (repetition): %r", text[:60])
        return ""
    # Catch implausibly long transcriptions from silence
    if len(text) > 200:
        log.warning("Whisper hallucination filtered (too long): %r", text[:60])
        return ""
    """Return '' if text looks like a Whisper hallucination."""
    cleaned = text.lower().strip().rstrip(".!?,")
    if cleaned in WHISPER_HALLUCINATIONS:
        log.warning("Whisper hallucination filtered: %r%s", text,
                    f" ({source})" if source else "")
        metrics.count("stt_hallucination", text=text, source=source or "mic")
        return ""
    return text


# ---------------------------------------------------------------------------
# Recording with VAD
# ---------------------------------------------------------------------------

def _record_utterance() -> tuple[bytes, str]:
    """Record from mic until trailing silence or the hard record cap.

    Returns ``(pcm_bytes, termination_reason)`` where ``termination_reason`` is
    one of:
      * ``"silence"``  — VAD detected ``cfg.silence_frames`` trailing silent
        frames after speech started (the normal, clean end of an utterance)
      * ``"max_cap"``  — hit ``cfg.max_record_seconds`` before trailing silence,
        i.e. the recording was likely cut short mid-sentence
      * ``"no_speech"``— the cap was hit but VAD never detected speech at all
        (silence-only capture)

    Trailing silence is ``cfg.silence_frames`` × ``FRAME_MS`` (deployed default
    25 × 30ms = 750ms; code default 15 × 30ms = 450ms).

    All blocking reads go through audio_mod.MicReader so a wedged USB mic (read
    never returns) raises MicStallError instead of hanging this thread forever.
    MicStallError subclasses RuntimeError, so it propagates to the wake/session
    loop's stall handling rather than silently stalling a live conversation.
    """
    import pyaudio

    vad = webrtcvad.Vad(cfg.vad_aggressiveness)
    pa  = audio_mod.get_pa()  # shared instance — DO NOT terminate
    frame_frames = int(SAMPLE_RATE * FRAME_MS / 1000)
    read_timeout_s = float(getattr(cfg, "mic_read_timeout_s", 10.0))

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=frame_frames,
        input_device_index=audio_mod.get_input_device_index(),
    )
    reader = audio_mod.MicReader(
        stream, frame_frames, read_timeout_s, name="stt-mic-reader"
    )

    frames       = []
    started      = False
    start_time   = time.time()
    silent_count = 0
    reason       = "max_cap"  # overwritten to "silence" on a clean VAD end

    try:
        # Flush mic buffer — discard post-playback reverb before VAD starts
        _flush_frames = max(1, round(cfg.post_play_flush_ms / FRAME_MS))
        for _ in range(_flush_frames):
            reader.read(read_timeout_s)

        while True:
            if time.time() - start_time > cfg.max_record_seconds:
                # Cap reached. If speech was detected we cut it short mid-sentence;
                # if not, it was a silence-only capture (no_speech).
                reason = "max_cap" if started else "no_speech"
                break
            data = reader.read(read_timeout_s)
            if not data:
                # Zero-length frame — no PCM to feed VAD. MicReader raises
                # MicStallError for a truly wedged read; an empty-but-returning
                # read just yields nothing this cycle, so skip it.
                continue
            frames.append(data)
            is_speech = vad.is_speech(data, SAMPLE_RATE)
            if is_speech:
                started      = True
                silent_count = 0
            elif started:
                silent_count += 1
                if silent_count >= cfg.silence_frames:
                    reason = "silence"
                    break
    finally:
        reader.stop()

    return b"".join(frames), reason


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def transcribe(audio_path: str) -> str:
    """Transcribe a WAV file. Returns text string."""
    _load_model()
    audio_array = _wav_to_array(audio_path)
    return _transcribe_array(audio_array)


def warm_up() -> None:
    """Pre-load STT model at startup to avoid ~5s init delay on first wake word."""
    _load_model()


def release() -> None:
    """Release the Hailo Speech2Text + VDevice after transcription, freeing the
    KV-Cache so the LLM can acquire the device.

    Mirrors Hailo's reference teardown (hailo-apps simple_whisper_chat.py):
    call the public ``.release()`` method on each object, Speech2Text first then
    the VDevice it was created on, each guarded independently.

    We deliberately do NOT use ``__exit__()`` + ``del`` + ``gc.collect()`` here.
    That path let the C++ VDevice destructor (``__del__``) fire a *second*
    release after ``__exit__`` had already freed the device — the most likely
    cause of the HAILO_INVALID_OPERATION(6) crash seen on 2026-05-19.
    ``.release()`` is the documented, idempotent public teardown call.
    """
    global _backend, _vdevice, _s2t
    with _model_lock:
        if _backend != "hailo":
            return
        s2t_ref, vdev_ref = _s2t, _vdevice
        _s2t = _vdevice = None
        _backend = None

    # Release Speech2Text before the VDevice it was created on (reference order).
    if s2t_ref is not None:
        try:
            s2t_ref.release()
        except Exception as e:
            log.debug("STT Speech2Text release error: %s", e)
    if vdev_ref is not None:
        try:
            vdev_ref.release()
        except Exception as e:
            log.debug("STT VDevice release error: %s", e)

    if _RELEASE_SETTLE_S > 0:
        time.sleep(_RELEASE_SETTLE_S)

    log.info("STT: Hailo Speech2Text + VDevice released (KV-Cache free)")


def listen_and_transcribe() -> str:
    """Record one utterance and return the transcribed text."""
    _load_model()

    with metrics.timer("stt_record"):
        pcm, term_reason = _record_utterance()

    # Directional signal for silence-timing tuning: "max_cap" means the utterance
    # was likely cut short mid-sentence (silence_frames/max_record_seconds too
    # tight). No ground truth — treat the counts as directional, not exact.
    metrics.count(
        "stt_cut_short",
        reason=term_reason,
        backend=_backend or "unknown",
        pcm_bytes=len(pcm),
    )

    if len(pcm) < FRAME_BYTES * 3:
        metrics.count("stt_empty", pcm_bytes=len(pcm))
        return ""

    audio_array = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    with metrics.timer("stt_transcribe", model=_active_model_name()):
        text = _transcribe_array(audio_array)

    return _filter_hallucination(text, source=_backend or "")


def transcribe_file(wav_path: str, prefer_cpu: bool = False) -> str:
    """Transcribe a pre-recorded WAV file (e.g. uploaded via web UI).

    prefer_cpu=True forces the CPU faster-whisper backend and never acquires the
    Hailo STT VDevice. The web UI (bender-web) runs in a separate process from
    bender-converse; if it grabbed the shared "SHARED"-group Hailo device it
    could collide with — or indefinitely starve — the conversation loop's
    STT/LLM turn-taking. This path is not latency-critical (a human clicking a
    button), so CPU is an acceptable, deterministic trade-off.
    """
    audio_array = _wav_to_array(wav_path)
    if prefer_cpu:
        model = _load_cpu_only_model()
        with metrics.timer("stt_transcribe", model=cfg.whisper_model, source="file_cpu"):
            text = _transcribe_cpu(model, audio_array)
        return _filter_hallucination(text, source="file")
    _load_model()
    with metrics.timer("stt_transcribe", model=_active_model_name(), source="file"):
        text = _transcribe_array(audio_array)
    return _filter_hallucination(text, source="file")


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _load_model()
    print(f"Backend: {_backend}  ({_active_model_name()})")
    _silence_ms = cfg.silence_frames * FRAME_MS
    print(f"Say something... (up to {cfg.max_record_seconds}s, stops after "
          f"{_silence_ms / 1000:.2f}s silence)")
    text = listen_and_transcribe()
    if text:
        print(f"You said: {text}")
    else:
        print("(nothing heard)")
