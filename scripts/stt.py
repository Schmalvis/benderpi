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
import hailo_hub
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
_vdevice   = None   # Hailo VDevice — legacy mode only; None when hailo_hub owns it
_s2t       = None   # Hailo Speech2Text instance
_cpu_model = None   # faster-whisper fallback
_cpu_only_model = None  # dedicated CPU model for prefer_cpu callers (never Hailo)
_model_lock = threading.Lock()  # guards _backend/_vdevice/_s2t mutations


def _load_model():
    """Initialise Hailo Whisper-Small as primary STT, falling back to CPU on failure."""
    global _backend, _vdevice, _s2t, _cpu_model
    with _model_lock:
        if _backend == "hailo":
            return
        if _backend == "cpu":
            # Re-offer the Hailo path before settling for CPU. In resident mode
            # release() no longer resets _backend after every turn, so without
            # this a single transient Hailo failure would pin the whole process
            # to CPU faster-whisper — median ~19.8s per utterance, p90 75s — until
            # a service restart. The hub's own 60s init cooldown makes this cheap
            # when Hailo is genuinely down, and upgrades us back the moment it
            # recovers.
            if getattr(cfg, "hailo_stt_enabled", True) and hailo_hub.enabled():
                s2t = hailo_hub.get_speech2text()
                if s2t is not None:
                    _s2t = s2t
                    _vdevice = None
                    _backend = "hailo"
                    log.info("STT recovered: back on Hailo Speech2Text (resident)")
            return

        # Resident mode (default): hailo_hub owns the VDevice and holds
        # Speech2Text loaded for the life of the process, so this runs once at
        # warm-up rather than before every transcription. _vdevice stays None —
        # we borrow the handle, we do not own it, and release() must not free it.
        if getattr(cfg, "hailo_stt_enabled", True) and hailo_hub.enabled():
            s2t = hailo_hub.get_speech2text()
            if s2t is not None:
                _s2t = s2t
                _vdevice = None
                _backend = "hailo"
                log.info("STT backend: Hailo Speech2Text (resident, via hailo_hub)")
                return
            log.warning("hailo_hub has no Speech2Text — falling back to CPU STT")

        # Legacy per-turn acquire/release path (cfg.hailo_resident = false).
        elif getattr(cfg, "hailo_stt_enabled", True) and os.path.exists(WHISPER_HEF):
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

        # CPU fallback — uses whisper_fallback_model (smaller/faster), NOT
        # whisper_model. This path only runs with the accelerator already down,
        # and on a 4GB Pi base.en measured a 27.3s median per utterance against
        # 845ms for tiny.en. A 27s transcription does not just feel dead, it also
        # poisons the session silence-timeout logic in wake_converse.py.
        try:
            from faster_whisper import WhisperModel
            _cpu_model = WhisperModel(_cpu_fallback_model_name(), device="cpu",
                                      compute_type="int8", cpu_threads=3)
            _backend = "cpu"
            log.warning("STT degraded to faster-whisper CPU (%s) — Hailo unavailable",
                        _cpu_fallback_model_name())
            return
        except Exception as e:
            log.warning("faster-whisper init failed (%s)", e)

        raise RuntimeError("No STT backend available")


def _cpu_fallback_model_name() -> str:
    """Model for the live-conversation CPU fallback. Falls back to
    ``whisper_model`` if the key is absent, so an old bender_config.json that
    predates the split still boots."""
    return getattr(cfg, "whisper_fallback_model", None) or cfg.whisper_model


def _active_model_name() -> str:
    """Model name for the stt_transcribe metric tag. Must track what actually
    ran, or the latency data attributes CPU-fallback timings to the wrong model
    — which is exactly how the 27s degraded path went unnoticed."""
    return "whisper-small-hailo" if _backend == "hailo" else _cpu_fallback_model_name()


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
    voiced       = []   # VAD-positive frames only — the speech-evidence sample
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
                voiced.append(data)
            elif started:
                silent_count += 1
                if silent_count >= cfg.silence_frames:
                    reason = "silence"
                    break
    finally:
        reader.stop()

    voiced_rms = 0
    if voiced:
        arr = np.frombuffer(b"".join(voiced), dtype=np.int16).astype(np.float64)
        if arr.size:
            voiced_rms = int(np.sqrt(np.mean(arr * arr)))
    stats = {"voiced_ms": len(voiced) * FRAME_MS, "voiced_rms": voiced_rms}
    return b"".join(frames), reason, stats


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
        # Resident mode: hailo_hub owns Speech2Text and the VDevice, and the LLM
        # coexists with it on-chip (Whisper does not touch the KV-Cache), so
        # there is nothing to free here. Returning early is the entire point of
        # the change — this call used to cost a ~2.5s Whisper reload on the next
        # turn. hailo_hub.close() releases at process exit.
        if _vdevice is None and hailo_hub.enabled():
            log.debug("STT release skipped — resident mode (hailo_hub owns the device)")
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
        pcm, term_reason, cap = _record_utterance()

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

    # Always record what the capture actually contained, whether or not it is
    # rejected below. The Hailo backend returns text with no confidence signals
    # (see _transcribe_cpu's docstring), so these two numbers are the *only*
    # evidence available for tuning the gates -- guessing thresholds without
    # them is how you end up with an assistant that ignores quiet speech.
    metrics.count("stt_capture", reason=term_reason,
                  backend=_backend or "unknown", **cap)

    # A capture in which VAD never once fired is silence: no speech happened.
    # Transcribing it anyway is pure hallucination risk for zero upside, and
    # until now it *was* transcribed -- the length check above passes because a
    # silence-only capture still runs the full max_record_seconds.
    if term_reason == "no_speech":
        metrics.count("stt_rejected", gate="no_speech", **cap)
        log.info("Discarded a silence-only capture (VAD never fired)")
        return ""

    # Below this much voiced audio it is a transient -- a door, a clatter, a
    # cough -- not an utterance. Deliberately permissive: "stop" and "bye" have
    # to survive, and over-gating (a Bender that ignores you) feels far worse
    # than the occasional answer to a noise. Tune from stt_capture metrics.
    min_ms = int(getattr(cfg, "stt_min_speech_ms", 0))
    if min_ms and cap["voiced_ms"] < min_ms:
        metrics.count("stt_rejected", gate="min_speech_ms", threshold=min_ms, **cap)
        log.info("Discarded capture with %dms of voiced audio (< %dms)",
                 cap["voiced_ms"], min_ms)
        return ""

    # Off by default (0). Background chatter and a TV carry across a room at a
    # much lower level than someone addressing the device, but the right floor
    # is room-specific, so it ships disabled with the data to set it.
    min_rms = int(getattr(cfg, "stt_min_speech_rms", 0))
    if min_rms and cap["voiced_rms"] < min_rms:
        metrics.count("stt_rejected", gate="min_speech_rms", threshold=min_rms, **cap)
        log.info("Discarded capture at RMS %d (< %d)", cap["voiced_rms"], min_rms)
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
