#!/usr/bin/env python3
"""
audio.py — WAV playback with LED amplitude visualisation.

The output stream is opened at session start and closed at session end.
This keeps the DAC warm between clips within a conversation (no click),
while freeing the audio device during wake-word listening so the mic
stream can operate without sample-rate conflicts on the WM8960.

API:
    open_session()   — open output stream (call after wake word detected)
    close_session()  — close output stream (call after session ends)
    play(wav_path)   — play a WAV file
"""

import os
import queue
import threading
import wave

import numpy as np
import pyaudio

from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("audio")

CHUNK        = int(cfg.audio_chunk)
SAMPLE_RATE  = 44100
CHANNELS     = 1
FORMAT       = pyaudio.paInt16

RMS_FLOOR    = int(cfg.audio_rms_floor)
RMS_CEILING  = int(cfg.audio_rms_ceiling)

# ---------------------------------------------------------------------------
# Device discovery — single source of truth for input/output indices.
# ---------------------------------------------------------------------------

def _list_devices(pa: "pyaudio.PyAudio") -> list[dict]:
    out = []
    for i in range(pa.get_device_count()):
        try:
            out.append(pa.get_device_info_by_index(i))
        except Exception:
            continue
    return out


def find_input_device(pa: "pyaudio.PyAudio", name_hints: list[str] | None = None) -> int | None:
    """Return the PortAudio index of the preferred input device, or None.

    Search order:
      1. Each hint in `name_hints` (case-insensitive substring match on device name)
         — first hit with maxInputChannels > 0 wins.
      2. Config key `input_device_name` (default 'mic_shared').
      3. Fallback substring 'seeed'.
    """
    hints = list(name_hints or [])
    hints.append(getattr(cfg, "input_device_name", "mic_shared"))
    hints.append("xvf_dsnoop")  # reSpeaker XVF3800 4-mic array
    hints.append("seeed")
    seen = set()
    for h in hints:
        if not h or h in seen:
            continue
        seen.add(h)
        for d in _list_devices(pa):
            if d.get("maxInputChannels", 0) <= 0:
                continue
            if h.lower() in str(d.get("name", "")).lower():
                log.info("find_input_device: matched '%s' -> idx=%d (%s)",
                         h, d["index"], d["name"])
                return int(d["index"])
    log.warning("find_input_device: no match for hints=%s — falling back to default", hints)
    return None


def find_output_device(pa: "pyaudio.PyAudio", name_hints: list[str] | None = None) -> int | None:
    """Return the PortAudio index of the preferred output device, or None.

    Same precedence as find_input_device but for output devices.
    """
    hints = list(name_hints or [])
    hints.append(getattr(cfg, "output_device_name", "seeed"))
    hints.append("default")
    seen = set()
    for h in hints:
        if not h or h in seen:
            continue
        seen.add(h)
        for d in _list_devices(pa):
            if d.get("maxOutputChannels", 0) <= 0:
                continue
            if h.lower() in str(d.get("name", "")).lower():
                log.info("find_output_device: matched '%s' -> idx=%d (%s)",
                         h, d["index"], d["name"])
                return int(d["index"])
    log.warning("find_output_device: no match for hints=%s — using PyAudio default", hints)
    return None


# Lazy cache — discovered on first call. Re-discovery requires service restart.
_INPUT_DEVICE: int | None = None
_OUTPUT_DEVICE: int | None = None


def get_input_device_index() -> int | None:
    """Return cached input device index, discovering on first call."""
    global _INPUT_DEVICE
    if _INPUT_DEVICE is None:
        _INPUT_DEVICE = find_input_device(_pa)
    return _INPUT_DEVICE


def get_output_device_index() -> int | None:
    """Return cached output device index, discovering on first call."""
    global _OUTPUT_DEVICE
    if _OUTPUT_DEVICE is None:
        _OUTPUT_DEVICE = find_output_device(_pa)
    return _OUTPUT_DEVICE


SILENCE_PRE  = cfg.silence_pre    # 0.02 from bender_config.json
SILENCE_POST = cfg.silence_post   # 0.08 from bender_config.json

log.debug("Audio config: silence_pre=%.3fs, silence_post=%.3fs", SILENCE_PRE, SILENCE_POST)

# Single shared PyAudio instance — never re-created to avoid PortAudio crashes
_pa    = pyaudio.PyAudio()
_stream = None
_lock  = threading.Lock()
_abort = threading.Event()


def get_pa() -> pyaudio.PyAudio:
    """Return the shared PyAudio instance (used by wake_converse for mic stream)."""
    return _pa


def abort():
    """Signal all in-progress playback to stop immediately."""
    _abort.set()


def was_aborted() -> bool:
    """Return True if the last play() call was aborted."""
    return _abort.is_set()


def _silence(duration_s: float) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    return b'\x00' * (n * 2 * CHANNELS)


def open_session():
    """Open the output stream. Call once after wake word is detected."""
    global _stream, _OUTPUT_DEVICE
    with _lock:
        if _stream is not None:
            try:
                if _stream.is_active():
                    return
            except Exception:
                pass
            try:
                _stream.close()
            except Exception:
                pass
        try:
            _stream = _pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                output_device_index=get_output_device_index(),
                frames_per_buffer=CHUNK,
            )
        except Exception as exc:
            log.error("open_session failed (%s) — re-discovering output device", exc)
            _OUTPUT_DEVICE = None  # force re-scan
            metrics.count("audio_open_retry")
            _stream = _pa.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                output_device_index=get_output_device_index(),
                frames_per_buffer=CHUNK,
            )
        # Warm up the DAC with a brief silence burst
        _stream.write(_silence(0.1))


def close_session():
    """Close the output stream. Call after session ends."""
    global _stream
    with _lock:
        if _stream is not None:
            try:
                _stream.write(_silence(0.1))  # drain tail
                _stream.stop_stream()
                _stream.close()
            except Exception:
                pass
            _stream = None


# ---------------------------------------------------------------------------
# RMS helpers for LED sync
# ---------------------------------------------------------------------------

def rms(data: bytes, sample_width: int) -> float:
    dtype = np.int16 if sample_width == 2 else np.int8
    samples = np.frombuffer(data, dtype=dtype).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def rms_to_ratio(value: float) -> float:
    clamped = max(0.0, value - RMS_FLOOR)
    return min(clamped / (RMS_CEILING - RMS_FLOOR), 1.0)


# ---------------------------------------------------------------------------
# MicReader — timeout-guarded blocking-read abstraction
# ---------------------------------------------------------------------------
#
# The failure this guards against: a wedged USB mic (reSpeaker XVF3800 unplugged
# or hung) makes PortAudio's blocking stream.read() never return. Any consumer
# that calls read() directly on the main thread then hangs forever — the stall
# checks and the systemd WATCHDOG=1 heartbeat downstream of it become dead code
# because they only run *after* read() returns.
#
# MicReader moves the blocking read onto a daemon thread that pushes frames onto
# a bounded queue. Consumers call read(timeout) which does queue.get(timeout=...)
# and raises MicStallError when no frame arrives in time — so the main thread
# stays live (can log, feed the watchdog, and escalate) even while the reader
# thread is wedged inside a C read() that will never return.
#
# Zombie-thread policy: a wedged read never returns, so on stop() we signal the
# thread to stop, best-effort close the stream from a *separate* timeout-guarded
# thread (closing a blocked stream can itself block), and then abandon the reader
# thread. It is a daemon, so a leaked wedged thread + its PortAudio stream will
# not keep the process alive. Repeated stalls leak one thread + one stream each;
# the escalation policy (reinit-once-then-exit) exists precisely so we never
# leak more than a bounded number before letting systemd restart the process.


class MicStallError(RuntimeError):
    """Raised when no mic frame arrives within the read timeout — the mic is
    presumed wedged (e.g. USB unplug/hang). Subclasses RuntimeError so existing
    ``except RuntimeError`` handlers (and the "stalled" reinit path in
    wake_converse) continue to catch it."""


class MicReader:
    """Reads a PortAudio input stream on a daemon thread, hands frames to the
    consumer via a bounded queue with a read timeout.

    Args:
        stream:      An open PyAudio input stream (or any object with
                     ``read(nframes, exception_on_overflow=...)`` and
                     ``stop_stream``/``close`` methods).
        frames_per_read: Number of frames to request per blocking read.
        timeout_s:   Default per-read timeout; a read that produces no frame in
                     this window raises MicStallError.
        maxsize:     Max queued frames before the reader thread blocks (back
                     pressure — bounds memory if the consumer stalls).
    """

    def __init__(self, stream, frames_per_read: int, timeout_s: float,
                 maxsize: int = 32, name: str = "mic-reader"):
        self._stream = stream
        self._frames_per_read = int(frames_per_read)
        self._timeout_s = float(timeout_s)
        self._q: "queue.Queue" = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._read_error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self):
        """Daemon loop: blocking-read frames and push them onto the queue.

        If read() wedges (USB unplug), this call never returns and the thread is
        abandoned by stop(). We tolerate a full queue by polling _stop so a
        stopped-but-not-consumed reader can exit instead of blocking on put().
        """
        while not self._stop.is_set():
            try:
                pcm = self._stream.read(
                    self._frames_per_read, exception_on_overflow=False
                )
            except Exception as exc:  # stream died mid-read (e.g. device gone)
                self._read_error = exc
                # Push a sentinel-free signal by unblocking the consumer's get()
                # via a zero-length frame; the consumer treats empty as no-PCM.
                try:
                    self._q.put(b"", timeout=0.5)
                except queue.Full:
                    pass
                return
            # Deliver the frame, but stay responsive to stop() if the consumer
            # has stopped draining (bounded queue would otherwise block forever).
            while not self._stop.is_set():
                try:
                    self._q.put(pcm, timeout=0.5)
                    break
                except queue.Full:
                    continue

    def read(self, timeout_s: float | None = None) -> bytes:
        """Return the next mic frame, or raise MicStallError on timeout.

        A zero-length frame (empty read) is returned as-is so the caller can
        apply its own zero-PCM stall accounting — MicStallError is reserved for
        the *no frame at all* case (a wedged blocking read)."""
        t = self._timeout_s if timeout_s is None else float(timeout_s)
        try:
            return self._q.get(timeout=t)
        except queue.Empty:
            if self._read_error is not None:
                raise MicStallError(
                    f"mic read failed: {self._read_error}"
                ) from self._read_error
            raise MicStallError(
                f"mic stalled: no frame in {t:.1f}s (reader thread wedged?)"
            )

    def stop(self, close_timeout_s: float = 2.0):
        """Signal the reader thread to stop and best-effort close the stream.

        Closing a stream whose read() is currently wedged can itself block, so
        the close is done on a separate short-lived thread guarded by
        close_timeout_s. If it doesn't return in time we abandon it — the reader
        thread is a daemon and won't keep the process alive."""
        self._stop.set()

        def _close():
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass

        closer = threading.Thread(target=_close, name="mic-reader-close", daemon=True)
        closer.start()
        closer.join(timeout=close_timeout_s)
        if closer.is_alive():
            log.warning("MicReader.stop: stream close still blocked after %.1fs "
                        "— abandoning wedged stream + reader thread", close_timeout_s)


def mic_selftest(duration_s: float = 1.0) -> dict:
    """Read ~duration_s of mic frames at startup and sanity-check the input path.

    Non-blocking to startup: on any failure this returns a result dict with
    ok=False and WARNs — the mic may still recover (e.g. USB re-enumerates), so
    we never abort the service here. Emits a `mic_selftest` metric.

    Checks:
      * read completed (no MicStallError) — mic not wedged
      * read-rate ≈ real-time (elapsed within 3x of requested duration)
      * at least one frame had non-zero RMS (mic producing signal, not silence-
        only or all-zero frames)

    Returns dict: {ok, reason, frames, elapsed_s, max_rms}.
    """
    frame_ms = 30
    rate = 16000
    frames_per_read = int(rate * frame_ms / 1000)
    want_frames = max(1, int((duration_s * 1000) / frame_ms))
    read_timeout_s = float(getattr(cfg, "mic_read_timeout_s", 10.0))

    pa = get_pa()
    input_device_index = get_input_device_index()
    device_name = ""
    try:
        if input_device_index is not None:
            device_name = pa.get_device_info_by_index(input_device_index)["name"]
    except Exception:
        pass
    capture_channels = 2 if "xvf_dsnoop" in device_name else 1

    result = {"ok": False, "reason": "unknown", "frames": 0,
              "elapsed_s": 0.0, "max_rms": 0.0}
    stream = None
    reader = None
    try:
        stream = pa.open(
            rate=rate, channels=capture_channels, format=FORMAT,
            input=True, frames_per_buffer=frames_per_read,
            input_device_index=input_device_index,
        )
        reader = MicReader(stream, frames_per_read, read_timeout_s,
                           name="mic-selftest-reader")
        import time as _time
        t0 = _time.monotonic()
        max_rms = 0.0
        read_ok = 0
        for _ in range(want_frames):
            data = reader.read(read_timeout_s)
            if not data:
                continue
            read_ok += 1
            samples = np.frombuffer(data, dtype=np.int16)
            if capture_channels == 2:
                samples = samples[::2]
            if len(samples):
                max_rms = max(max_rms, float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))))
        elapsed = _time.monotonic() - t0
        result.update(frames=read_ok, elapsed_s=round(elapsed, 3),
                      max_rms=round(max_rms, 1))

        if read_ok == 0:
            result["reason"] = "no frames read"
        elif elapsed > 3.0 * duration_s:
            result["reason"] = f"read-rate too slow ({elapsed:.2f}s for {duration_s:.1f}s of audio)"
        elif max_rms <= 0.0:
            result["reason"] = "all-zero frames (mic silent / disconnected?)"
        else:
            result["ok"] = True
            result["reason"] = "ok"
    except MicStallError as exc:
        result["reason"] = f"mic stalled: {exc}"
    except Exception as exc:
        result["reason"] = f"mic self-test error: {exc}"
    finally:
        if reader is not None:
            reader.stop()
        elif stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    metrics.count("mic_selftest", ok=result["ok"], reason=result["reason"],
                  frames=result["frames"], max_rms=result["max_rms"])
    if result["ok"]:
        log.info("Mic self-test OK: %d frames in %.2fs, max_rms=%.0f (mic: %s)",
                 result["frames"], result["elapsed_s"], result["max_rms"],
                 device_name or "default")
    else:
        log.warning("Mic self-test FAILED (%s) — continuing anyway, mic may recover "
                    "(mic: %s, frames=%d, max_rms=%.0f)",
                    result["reason"], device_name or "default",
                    result["frames"], result["max_rms"])
    return result


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

def play(wav_path: str, on_chunk=None, on_done=None):
    """
    Play a WAV file.
    open_session() must be called first.

    Args:
        wav_path:  Path to the WAV file to play.
        on_chunk:  Optional callback called for each audio chunk with a
                   normalised amplitude value in [0.0, 1.0].
        on_done:   Optional callback called once after playback finishes
                   (always invoked outside _lock).
    """
    with metrics.timer("audio_play"):
        with _lock:
            _abort.clear()
            if _stream is None or not _stream.is_active():
                # Fallback: reopen if session stream was lost
                open_session()

            _stream.write(_silence(SILENCE_PRE))

            with wave.open(wav_path, 'rb') as wf:
                sw = wf.getsampwidth()
                data = wf.readframes(CHUNK)
                while data:
                    if _abort.is_set():
                        log.info("Playback aborted: %s", wav_path)
                        break
                    _stream.write(data)
                    if on_chunk:
                        on_chunk(rms_to_ratio(rms(data, sw)))
                    data = wf.readframes(CHUNK)

            if not _abort.is_set():
                _stream.write(_silence(SILENCE_POST))

    if on_done:
        on_done()


def play_oneshot(wav_path: str, on_chunk=None, on_done=None):
    """Open stream, play clip, close stream. For use outside a session.
    Thread-safe — blocks behind _lock if a session is active.

    Args:
        wav_path:  Path to the WAV file to play.
        on_chunk:  Optional callback called for each audio chunk with a
                   normalised amplitude value in [0.0, 1.0].
        on_done:   Optional callback called once after playback finishes
                   (always invoked outside _lock).
    """
    with _lock:
        _abort.clear()
        was_open = _stream is not None
        if was_open:
            try:
                was_open = _stream.is_active()
            except Exception:
                was_open = False

        if not was_open:
            stream = _pa.open(
                format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                output=True, output_device_index=get_output_device_index(),
                frames_per_buffer=CHUNK,
            )
        else:
            stream = _stream

        try:
            stream.write(_silence(SILENCE_PRE))
            with wave.open(wav_path, 'rb') as wf:
                sw = wf.getsampwidth()
                data = wf.readframes(CHUNK)
                while data:
                    if _abort.is_set():
                        log.info("Playback aborted: %s", wav_path)
                        break
                    stream.write(data)
                    if on_chunk:
                        on_chunk(rms_to_ratio(rms(data, sw)))
                    data = wf.readframes(CHUNK)
            if not _abort.is_set():
                stream.write(_silence(SILENCE_POST))
        finally:
            if not was_open:
                stream.stop_stream()
                stream.close()
    if on_done:
        on_done()


def play_stream_oneshot(wav_iter, on_chunk=None, on_done=None):
    """Open stream, play WAV clips from an iterator back-to-back, close stream.
    For use outside a session (camera responses, passive vision). Thread-safe —
    blocks behind _lock if a session is active. Unlinks each WAV after playing.
    Closes the generator on abort to trigger cleanup of unconsumed futures.

    Args:
        wav_iter:  Iterator yielding WAV file paths (e.g. speak_streaming()).
        on_chunk:  Optional callback(amplitude: float) per chunk, value in [0.0, 1.0].
        on_done:   Optional callback called once after all clips finish (or abort).
    """
    gen = iter(wav_iter)
    with _lock:
        _abort.clear()
        was_open = _stream is not None
        if was_open:
            try:
                was_open = _stream.is_active()
            except Exception:
                was_open = False

        if not was_open:
            stream = _pa.open(
                format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                output=True, output_device_index=get_output_device_index(),
                frames_per_buffer=CHUNK,
            )
        else:
            stream = _stream

        try:
            stream.write(_silence(SILENCE_PRE))
            for wav_path in gen:
                if _abort.is_set():
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                    if hasattr(gen, "close"):
                        gen.close()  # triggers BaseException cleanup in speak_streaming
                    break
                try:
                    with wave.open(wav_path, 'rb') as wf:
                        sw = wf.getsampwidth()
                        data = wf.readframes(CHUNK)
                        while data:
                            if _abort.is_set():
                                break
                            stream.write(data)
                            if on_chunk:
                                on_chunk(rms_to_ratio(rms(data, sw)))
                            data = wf.readframes(CHUNK)
                finally:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
            if not _abort.is_set():
                stream.write(_silence(SILENCE_POST))
        finally:
            if not was_open:
                stream.stop_stream()
                stream.close()
    if on_done:
        on_done()


def play_stream(wav_iter, on_chunk=None, on_done=None):
    """
    Play WAV files from an iterator, back-to-back with no gap between sentences.
    Starts playing as soon as the first WAV is yielded; remaining sentences play
    as they arrive. Handles abort cleanly. Caller's iterator should yield temp
    file paths; this function unlinks each after playing.

    open_session() must be called first.
    """
    with metrics.timer("audio_play"):
        with _lock:
            _abort.clear()
            if _stream is None or not _stream.is_active():
                open_session()

            _stream.write(_silence(SILENCE_PRE))

            for wav_path in wav_iter:
                if _abort.is_set():
                    # Drain remaining paths and clean up
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                    for remaining in wav_iter:
                        try:
                            os.unlink(remaining)
                        except OSError:
                            pass
                    break

                try:
                    with wave.open(wav_path, 'rb') as wf:
                        sw = wf.getsampwidth()
                        data = wf.readframes(CHUNK)
                        while data:
                            if _abort.is_set():
                                break
                            _stream.write(data)
                            if on_chunk:
                                on_chunk(rms_to_ratio(rms(data, sw)))
                            data = wf.readframes(CHUNK)
                finally:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass

            if not _abort.is_set():
                _stream.write(_silence(SILENCE_POST))

    if on_done:
        on_done()

