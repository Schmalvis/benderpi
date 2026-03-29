#!/usr/bin/env python3
"""
tts_generate.py — Piper TTS inference wrapper for Bender voice.

Post-processes Piper output:
  - Resamples from 22050Hz → 44100Hz to match real Bender clips
  - Adds 50ms silence padding at start and end to prevent audio pops

Usage:
    import tts_generate
    wav_path = tts_generate.speak("Bite my shiny metal ass!")
    # wav_path is a temp file — caller is responsible for playing and cleanup
"""

import json
import os
import queue
import re
import subprocess
import tempfile
import time
import wave
import numpy as np

from logger import get_logger
from metrics import metrics

log = get_logger("tts")

from config import cfg

def _find_repo_root() -> str:
    """Return the main (common) git repo root, works from both worktrees and main checkout."""
    try:
        git_common = subprocess.check_output(
            ["git", "-C", os.path.dirname(os.path.abspath(__file__)), "rev-parse", "--git-common-dir"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        # git-common-dir is relative to cwd or absolute; resolve against scripts dir
        base = os.path.dirname(os.path.abspath(__file__))
        common_abs = os.path.normpath(os.path.join(base, git_common))
        return os.path.abspath(os.path.join(common_abs, ".."))
    except Exception:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_REPO_ROOT = _find_repo_root()
PIPER_BIN  = os.path.join(_REPO_ROOT, "piper", "piper")
MODEL_PATH = os.path.join(_REPO_ROOT, "models", "bender.onnx")

TARGET_RATE  = 44100   # match real Bender clips
SILENCE_PRE  = 0.0    # audio.py adds pre-silence for all clips
SILENCE_POST = 0.0    # audio.py adds post-silence for all clips


def _resample_and_pad(in_path: str, out_path: str):
    """Resample WAV to TARGET_RATE and pad with silence."""
    from scipy.signal import resample_poly
    from math import gcd

    with wave.open(in_path, 'rb') as wf:
        src_rate   = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth  = wf.getsampwidth()
        raw        = wf.readframes(wf.getnframes())

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

    # Resample if needed
    if src_rate != TARGET_RATE:
        g  = gcd(TARGET_RATE, src_rate)
        up = TARGET_RATE // g
        dn = src_rate    // g
        samples = resample_poly(samples, up, dn)

    # Silence padding (samples)
    pre_pad  = np.zeros(int(TARGET_RATE * SILENCE_PRE),  dtype=np.float32)
    post_pad = np.zeros(int(TARGET_RATE * SILENCE_POST), dtype=np.float32)
    samples  = np.concatenate([pre_pad, samples, post_pad])

    # De-ess: gently attenuate harsh high-frequency content above 7kHz
    from scipy.signal import butter, sosfilt
    nyq = TARGET_RATE / 2
    sos = butter(2, 7000 / nyq, btype='high', output='sos')
    highs = sosfilt(sos, samples.astype(np.float64)).astype(np.float32)
    samples = samples - highs * 0.4  # reduce >7kHz by ~4dB

    # Clip and convert back to int16
    samples = np.clip(samples, -32768, 32767).astype(np.int16)

    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(samples.tobytes())


# ---------------------------------------------------------------------------
# Persistent Piper process pool
# ---------------------------------------------------------------------------

_PIPER_POOL_SIZE = 3  # matches ThreadPoolExecutor max_workers in speak_streaming / speak_from_iter


class _PiperProcess:
    """One persistent piper process. NOT thread-safe — use via PiperPool."""

    def __init__(self):
        self._proc = None
        self._start()

    def _start(self):
        if not os.path.exists(PIPER_BIN):
            raise FileNotFoundError(f"Piper binary not found: {PIPER_BIN}")
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(f"Bender model not found: {MODEL_PATH}")
        piper_dir = os.path.dirname(PIPER_BIN)
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = piper_dir + ":" + env.get("LD_LIBRARY_PATH", "")
        self._proc = subprocess.Popen(
            [
                PIPER_BIN,
                "--model", MODEL_PATH,
                "--json-input",
                "--length_scale", str(cfg.speech_rate),
                "--noise_scale", str(cfg.tts_noise_scale),
                "--noise_scale_w", str(cfg.tts_noise_scale_w),
                "--quiet",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )

    def synthesize(self, text: str) -> str:
        """Write text to piper, return path to raw WAV (22050Hz). Caller unlinks."""
        if self._proc.poll() is not None:
            log.warning("Piper process died — restarting")
            self._start()

        out_path = tempfile.mktemp(suffix=".wav", dir="/tmp")
        payload = (json.dumps({"text": text, "output_file": out_path}) + "\n").encode()

        try:
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
        except BrokenPipeError:
            log.warning("Piper stdin broken — restarting")
            self._start()
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()

        # Piper writes the file completely before reading the next line.
        # Poll until the file exists with a stable size (write complete).
        deadline = time.monotonic() + 10.0
        prev_size = -1
        while time.monotonic() < deadline:
            if os.path.exists(out_path):
                size = os.path.getsize(out_path)
                if size > 0 and size == prev_size:
                    return out_path
                prev_size = size
            time.sleep(0.005)
        raise TimeoutError(f"Piper synthesis timed out for: {text!r}")

    def close(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=2.0)
            except Exception:
                self._proc.kill()
        self._proc = None


class PiperPool:
    """Thread-safe pool of persistent Piper processes."""

    def __init__(self, size: int = _PIPER_POOL_SIZE):
        self._q: queue.Queue = queue.Queue()
        for _ in range(size):
            self._q.put(_PiperProcess())
        log.info("PiperPool: %d persistent processes ready", size)

    def synthesize(self, text: str) -> str:
        """Borrow a process, synthesize, return process to pool."""
        proc = self._q.get()
        try:
            return proc.synthesize(text)
        finally:
            self._q.put(proc)

    def close(self):
        while not self._q.empty():
            try:
                proc = self._q.get_nowait()
                proc.close()
            except Exception:
                pass


_piper_pool: "PiperPool | None" = None


def _get_piper_pool() -> "PiperPool":
    global _piper_pool
    if _piper_pool is None:
        _piper_pool = PiperPool()
    return _piper_pool


# ---------------------------------------------------------------------------


def _speak_single(text: str) -> str:
    """
    Generate TTS audio for a single sentence. Returns path to a temp WAV file at 44100Hz.
    Caller is responsible for playing and cleanup.
    """
    with metrics.timer("tts_generate"):
        raw_path = _get_piper_pool().synthesize(text)

        # Post-process: resample + pad → final temp file
        out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_tmp.close()
        try:
            _resample_and_pad(raw_path, out_tmp.name)
        finally:
            os.unlink(raw_path)

        return out_tmp.name



_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')

def _preprocess_text(text: str) -> str:
    """Normalise text for natural TTS delivery."""
    # Strip markdown bold/italic
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
    # Em-dash and en-dash → comma pause
    text = re.sub(r'\s*[–—]\s*', ', ', text)
    # Ellipsis → pause
    text = text.replace('...', ', ')
    # Multiple spaces
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def speak(text: str) -> str:
    """
    Generate TTS audio for text, splitting on sentence boundaries for
    more natural prosody. Returns path to a concatenated temp WAV at 44100Hz.
    Caller is responsible for playing and cleanup.
    """
    text = _preprocess_text(text)
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    if len(sentences) <= 1:
        return _speak_single(text)

    # Generate sentences in parallel — each borrows from the shared PiperPool
    import wave
    from concurrent.futures import ThreadPoolExecutor
    parts = []
    try:
        with ThreadPoolExecutor(max_workers=min(len(sentences), 3)) as pool:
            futures = [pool.submit(_speak_single, s) for s in sentences]
            parts = [f.result() for f in futures]  # preserves order, total time = max(sentence times)

        # Read all WAVs and concatenate frames
        out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_tmp.close()
        frames = b""
        params = None
        for p in parts:
            with wave.open(p, 'rb') as wf:
                if params is None:
                    params = wf.getparams()
                frames += wf.readframes(wf.getnframes())

        with wave.open(out_tmp.name, 'wb') as wf:
            wf.setparams(params)
            wf.writeframes(frames)
        return out_tmp.name
    finally:
        for p in parts:
            try:
                os.unlink(p)
            except OSError:
                pass



def speak_streaming(text: str):
    """
    Generate TTS audio sentence-by-sentence, yielding WAV paths as each is ready.
    Sentence 1 is yielded as soon as Piper finishes it; sentences 2+ run concurrently.
    Caller is responsible for playing and unlinking each yielded path.
    """
    from concurrent.futures import ThreadPoolExecutor
    text = _preprocess_text(text)
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    if not sentences:
        return
    if len(sentences) == 1:
        yield _speak_single(sentences[0])
        return
    # Submit all sentences concurrently; yield in order as each completes
    with ThreadPoolExecutor(max_workers=min(len(sentences), 3)) as pool:
        futures = [pool.submit(_speak_single, s) for s in sentences]
        try:
            for future in futures:
                yield future.result()  # preserves sentence order; blocks only until each is ready
        except Exception:
            # Clean up temp files from any futures that already completed
            for f in futures:
                if f.done() and not f.cancelled():
                    try:
                        result = f.result()
                        os.unlink(result)
                    except Exception:
                        pass
            raise


def speak_from_iter(sentence_iter):
    """Generate TTS for sentences from an iterator (e.g. streaming LLM output).
    Submits TTS for sentence N+1 concurrently while sentence N is being played.
    Yields WAV paths in sentence order. Caller must unlink each yielded path.
    """
    from concurrent.futures import ThreadPoolExecutor
    import collections

    pending = collections.deque()

    _error_occurred = False
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for sentence in sentence_iter:
                # Submit this sentence's TTS immediately
                pending.append(pool.submit(_speak_single, sentence))
                # If we have a backlog, yield the oldest completed future
                if len(pending) >= 2:
                    yield pending.popleft().result()
            # Flush remaining
            while pending:
                yield pending.popleft().result()
    except Exception:
        _error_occurred = True
        raise
    finally:
        if _error_occurred:
            # Pool has already shut down (ThreadPoolExecutor.__exit__ ran); all futures done
            for f in list(pending):
                if not f.cancelled():
                    try:
                        os.unlink(f.result())
                    except Exception:
                        pass


def warm_up():
    """Pre-warm Piper by running a dummy synthesis. Call at service start."""
    log.info("Warming up Piper TTS...")
    try:
        wav = speak("test")
        os.unlink(wav)
        log.info("Piper warm-up complete")
    except Exception as e:
        log.warning("Piper warm-up failed (expected if not on Pi): %s", e)
