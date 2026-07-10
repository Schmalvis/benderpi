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

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
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

# Piper's native output rate for the Bender voice model is always 22050Hz, so
# the 22050 -> 44100 resample is always a clean 2:1 upsample. Kept as a fast
# path constant; a src_rate that doesn't match falls back to the generic
# gcd-based ratio below (defensive, in case the model is ever swapped for one
# with a different native rate).
_PIPER_NATIVE_RATE = 22050
_RESAMPLE_UP, _RESAMPLE_DOWN = 2, 1  # TARGET_RATE // gcd, _PIPER_NATIVE_RATE // gcd

# scipy import is ~100ms+ cold; module-level "lazy but cached" globals so it
# only pays that cost once per process instead of once per TTS call. The
# de-ess filter's SOS coefficients only depend on TARGET_RATE, so they're
# designed once too instead of being recomputed by butter() on every sentence.
_scipy_signal = None
_DEESS_SOS = None


def _get_scipy_signal():
    global _scipy_signal
    if _scipy_signal is None:
        import scipy.signal as sp_signal
        _scipy_signal = sp_signal
    return _scipy_signal


def _get_deess_sos():
    global _DEESS_SOS
    if _DEESS_SOS is None:
        sp = _get_scipy_signal()
        nyq = TARGET_RATE / 2
        _DEESS_SOS = sp.butter(2, 7000 / nyq, btype='high', output='sos')
    return _DEESS_SOS


def _resample_and_pad(in_path: str, out_path: str):
    """Resample WAV to TARGET_RATE and pad with silence."""
    sp = _get_scipy_signal()

    with wave.open(in_path, 'rb') as wf:
        src_rate   = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth  = wf.getsampwidth()
        raw        = wf.readframes(wf.getnframes())

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

    # Resample if needed
    if src_rate != TARGET_RATE:
        if src_rate == _PIPER_NATIVE_RATE:
            up, dn = _RESAMPLE_UP, _RESAMPLE_DOWN
        else:
            from math import gcd
            g  = gcd(TARGET_RATE, src_rate)
            up = TARGET_RATE // g
            dn = src_rate    // g
        samples = sp.resample_poly(samples, up, dn)

    # Silence padding (samples)
    pre_pad  = np.zeros(int(TARGET_RATE * SILENCE_PRE),  dtype=np.float32)
    post_pad = np.zeros(int(TARGET_RATE * SILENCE_POST), dtype=np.float32)
    samples  = np.concatenate([pre_pad, samples, post_pad])

    # De-ess: gently attenuate harsh high-frequency content above 7kHz
    highs = sp.sosfilt(_get_deess_sos(), samples.astype(np.float64)).astype(np.float32)
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
_PIPER_SYNTH_TIMEOUT_S = 10.0        # max time to wait for one sentence's WAV to land
_PIPER_POOL_BORROW_TIMEOUT_S = 15.0  # synth deadline + margin — caps how long a caller
                                      # blocks waiting for a free pool process before
                                      # failing instead of queueing forever behind a
                                      # wedged process shared with another caller
                                      # (e.g. web puppet vs. the conversation loop)


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


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

    def _restart(self, reason: str, *, force_kill: bool = False):
        """Restart the underlying process. If it might still be alive (e.g. a
        hang, not a crash), kill it first so we don't leak the old one."""
        metrics.count("tts_proc_restart", reason=reason)
        if force_kill and self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2.0)
            except Exception:
                pass
        self._start()

    def synthesize(self, text: str) -> str:
        """Write text to piper, return path to raw WAV (22050Hz). Caller unlinks."""
        if self._proc.poll() is not None:
            log.warning("Piper process died (exit=%s) — restarting", self._proc.returncode)
            self._restart("dead_on_borrow")

        fd, out_path = tempfile.mkstemp(suffix=".wav", dir="/tmp")
        os.close(fd)
        os.unlink(out_path)  # let Piper create it
        payload = (json.dumps({"text": text, "output_file": out_path}) + "\n").encode()

        try:
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
        except BrokenPipeError:
            log.warning("Piper stdin broken — restarting")
            self._restart("broken_pipe")
            try:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
            except BrokenPipeError as e:
                # Cap restart attempts at one per borrow (e.g. OOM killer taking
                # the freshly-spawned process too) — raise rather than loop.
                _safe_unlink(out_path)
                raise RuntimeError(
                    f"Piper stdin broken after restart for: {text!r}"
                ) from e

        # Piper writes the file completely before reading the next line.
        # Poll until the file exists with a stable size (write complete), while
        # also watching the process itself: a mid-inference crash then fails in
        # ~5ms (one poll interval) instead of burning the full deadline.
        deadline = time.monotonic() + _PIPER_SYNTH_TIMEOUT_S
        prev_size = -1
        while time.monotonic() < deadline:
            exit_code = self._proc.poll()
            if exit_code is not None:
                log.warning("Piper crashed mid-synthesis (exit=%s) — restarting", exit_code)
                _safe_unlink(out_path)
                self._restart("crash_mid_synthesis")
                raise RuntimeError(f"Piper crashed during synthesis for: {text!r}")
            if os.path.exists(out_path):
                size = os.path.getsize(out_path)
                if size > 44 and size == prev_size:
                    return out_path
                prev_size = size
            time.sleep(0.005)

        # Timed out with the process still alive — it's wedged; discard the
        # partial output and restart in place so the *next* borrow gets a
        # healthy process instead of hanging again.
        log.warning("Piper synthesis timed out — restarting (possibly wedged)")
        _safe_unlink(out_path)
        self._restart("timeout", force_kill=True)
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

    def synthesize(self, text: str, timeout: float = _PIPER_POOL_BORROW_TIMEOUT_S) -> str:
        """Borrow a process, synthesize, return process to pool.

        Bounded wait for a free process: without this, a wedged process
        (still checked out by a stuck caller) can starve every other caller
        indefinitely — e.g. the web puppet and the live conversation loop
        share this pool and must not be able to block each other forever.
        """
        try:
            proc = self._q.get(timeout=timeout)
        except queue.Empty:
            metrics.count("tts_pool_starved")
            raise TimeoutError(
                f"Piper pool starved — no free process within {timeout:g}s"
            )
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
_piper_pool_lock = threading.Lock()


def _get_piper_pool() -> "PiperPool":
    global _piper_pool
    if _piper_pool is None:
        with _piper_pool_lock:
            if _piper_pool is None:
                _piper_pool = PiperPool()
    return _piper_pool


# ---------------------------------------------------------------------------
# Content-hash disk cache (post-processed 44.1kHz WAV, per sentence)
# ---------------------------------------------------------------------------
#
# cfg is an import-time singleton, immutable for the lifetime of a running
# bender-converse process (scripts/web/routes/config.py's PUT /api/config
# writes bender_config.json but returns restart_required — it never mutates
# the running process's cfg). The pooled Piper processes are spawned from
# this same immutable cfg (see _PiperProcess._start's --length_scale etc.),
# so reading cfg fresh here at call time can never desync from what the pool
# actually synthesizes: both always see the same values for the process's
# whole lifetime, including across an in-place crash restart.


def _cache_key(text: str) -> str:
    """sha256 of text + voice params + model file, so a speech-rate/noise-scale
    tweak (or a swapped model) invalidates old cache entries instead of
    serving stale-sounding audio forever."""
    material = "\x1f".join([
        text,
        f"speech_rate={cfg.speech_rate}",
        f"noise_scale={cfg.tts_noise_scale}",
        f"noise_scale_w={cfg.tts_noise_scale_w}",
        f"model={os.path.basename(MODEL_PATH)}",
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cache_path(cache_key: str) -> str:
    return os.path.join(cfg.tts_cache_dir, f"{cache_key}.wav")


def _cache_get(cache_key: str) -> "str | None":
    """Return a fresh temp copy of the cached WAV, or None on miss.

    Never returns the cache file's own path — callers unlink whatever they
    receive, so handing out the cache file itself would delete the cache
    entry on first use.
    """
    path = _cache_path(cache_key)
    if not os.path.exists(path):
        return None
    try:
        out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_tmp.close()
        shutil.copyfile(path, out_tmp.name)
        os.utime(path, None)  # touch mtime — keeps hot entries alive under LRU prune
        return out_tmp.name
    except OSError as e:
        log.warning("TTS cache read failed for %s: %s", cache_key, e)
        return None


def _cache_put(cache_key: str, src_path: str) -> None:
    """Store a copy of src_path's content under cache_key. Atomic (write to a
    temp file in the cache dir, then os.replace) so a concurrent reader never
    sees a partially-written cache file."""
    try:
        os.makedirs(cfg.tts_cache_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(suffix=".wav", dir=cfg.tts_cache_dir)
        os.close(fd)
        shutil.copyfile(src_path, tmp_path)
        os.replace(tmp_path, _cache_path(cache_key))
        _prune_cache()
    except OSError as e:
        log.warning("TTS cache write failed for %s: %s", cache_key, e)


def _prune_cache() -> None:
    """Size-capped LRU-by-mtime prune, bounding disk usage on the SD card."""
    max_bytes = float(cfg.tts_cache_max_mb) * 1024 * 1024
    try:
        entries = []
        total = 0
        with os.scandir(cfg.tts_cache_dir) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                st = entry.stat()
                entries.append((st.st_mtime, st.st_size, entry.path))
                total += st.st_size
        if total <= max_bytes:
            return
        entries.sort(key=lambda e: e[0])  # oldest mtime first
        for _mtime, size, path in entries:
            if total <= max_bytes:
                break
            try:
                os.unlink(path)
                total -= size
            except OSError:
                pass
    except OSError as e:
        log.warning("TTS cache prune failed: %s", e)


def _speak_single(text: str) -> str:
    """
    Generate TTS audio for a single sentence. Returns path to a temp WAV file at 44100Hz.
    Caller is responsible for playing and cleanup.
    """
    with metrics.timer("tts_generate"):
        cache_key = _cache_key(text)
        cached = _cache_get(cache_key)
        if cached is not None:
            metrics.count("tts_cache_hit")
            return cached
        metrics.count("tts_cache_miss")

        raw_path = _get_piper_pool().synthesize(text)

        # Post-process: resample + pad → final temp file
        out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_tmp.close()
        try:
            _resample_and_pad(raw_path, out_tmp.name)
        finally:
            os.unlink(raw_path)

        _cache_put(cache_key, out_tmp.name)
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
    
    Properly cleans up temp files if the generator is closed early (GeneratorExit)
    or if an exception occurs during synthesis.
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
            yielded_count = 0
            for future in futures:
                result = future.result()
                yield result
                yielded_count += 1
        except BaseException:
            # Clean up temp files from futures that were NOT yielded.
            # Call f.result() unconditionally (blocks until done) — the thread pool
            # is still running at this point so all futures will complete.
            for f in futures[yielded_count:]:
                if not f.cancelled():
                    try:
                        os.unlink(f.result())
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
