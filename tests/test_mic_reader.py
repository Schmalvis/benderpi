"""Unit tests for audio.MicReader — the timeout-guarded blocking-read wrapper.

MicReader moves a blocking PortAudio read onto a daemon thread and delivers
frames via a queue with a read timeout, so a wedged USB mic (read never returns)
raises MicStallError on the consumer thread instead of hanging it forever.
"""
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, "scripts")

# Fake pyaudio so `import audio` succeeds without hardware.
_fake_pyaudio = types.SimpleNamespace(
    paInt16=8,
    PyAudio=lambda: types.SimpleNamespace(),
)
sys.modules.setdefault("pyaudio", _fake_pyaudio)

import audio  # noqa: E402
from audio import MicReader, MicStallError  # noqa: E402


class NormalStream:
    """Returns a fixed frame immediately on every read."""

    def __init__(self, frame=b"\x01\x02"):
        self.frame = frame
        self.reads = 0
        self.closed = False

    def read(self, n, exception_on_overflow=False):
        self.reads += 1
        return self.frame

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class DelayedStream:
    """First read is slow (delay_s), subsequent reads are instant."""

    def __init__(self, delay_s):
        self.delay_s = delay_s
        self.reads = 0

    def read(self, n, exception_on_overflow=False):
        if self.reads == 0:
            time.sleep(self.delay_s)
        self.reads += 1
        return b"\x03\x04"

    def stop_stream(self):
        pass

    def close(self):
        pass


class HangStream:
    """read() never returns — simulates a wedged USB blocking read."""

    def __init__(self):
        self.entered = threading.Event()

    def read(self, n, exception_on_overflow=False):
        self.entered.set()
        time.sleep(3600)

    def stop_stream(self):
        pass

    def close(self):
        pass


class EmptyStream:
    """Returns zero-length frames immediately (e.g. USB present but no data)."""

    def read(self, n, exception_on_overflow=False):
        return b""

    def stop_stream(self):
        pass

    def close(self):
        pass


class RaisingStream:
    """read() raises — simulates the device disappearing mid-read."""

    def read(self, n, exception_on_overflow=False):
        raise OSError("device disconnected")

    def stop_stream(self):
        pass

    def close(self):
        pass


def test_normal_frames_delivered():
    stream = NormalStream(frame=b"abcd")
    reader = MicReader(stream, frames_per_read=2, timeout_s=1.0)
    try:
        for _ in range(5):
            assert reader.read(timeout_s=1.0) == b"abcd"
    finally:
        reader.stop()


def test_delayed_frame_within_timeout():
    stream = DelayedStream(delay_s=0.2)
    reader = MicReader(stream, frames_per_read=2, timeout_s=1.0)
    try:
        # First frame is slow but arrives before the 1.0s timeout.
        assert reader.read(timeout_s=1.0) == b"\x03\x04"
    finally:
        reader.stop()


def test_permanent_hang_raises_mic_stall():
    stream = HangStream()
    reader = MicReader(stream, frames_per_read=2, timeout_s=0.2)
    try:
        assert stream.entered.wait(timeout=1.0), "reader thread never entered read()"
        start = time.monotonic()
        with pytest.raises(MicStallError):
            reader.read(timeout_s=0.2)
        elapsed = time.monotonic() - start
        # Consumer returned promptly on timeout even though read() is wedged.
        assert elapsed < 1.0
    finally:
        reader.stop()


def test_empty_reads_delivered_not_stalled():
    """Zero-length frames must be returned (not raised) so the caller can apply
    its own no-PCM stall accounting."""
    stream = EmptyStream()
    reader = MicReader(stream, frames_per_read=2, timeout_s=0.5)
    try:
        assert reader.read(timeout_s=0.5) == b""
    finally:
        reader.stop()


def test_read_error_surfaces_as_mic_stall():
    stream = RaisingStream()
    reader = MicReader(stream, frames_per_read=2, timeout_s=0.5)
    try:
        # First get() returns the sentinel empty frame the reader pushes on error.
        first = reader.read(timeout_s=0.5)
        assert first == b""
        # Subsequent read has no frames and a recorded error → MicStallError.
        with pytest.raises(MicStallError):
            reader.read(timeout_s=0.3)
    finally:
        reader.stop()


def test_stop_is_idempotent_and_nonblocking_on_hang():
    stream = HangStream()
    reader = MicReader(stream, frames_per_read=2, timeout_s=0.2)
    assert stream.entered.wait(timeout=1.0)
    start = time.monotonic()
    reader.stop(close_timeout_s=0.5)   # must return even though read() is wedged
    reader.stop(close_timeout_s=0.5)   # idempotent
    assert time.monotonic() - start < 2.0


def test_stop_closes_normal_stream():
    stream = NormalStream()
    reader = MicReader(stream, frames_per_read=2, timeout_s=0.5)
    time.sleep(0.05)
    reader.stop(close_timeout_s=1.0)
    # Give the closer thread a moment.
    time.sleep(0.1)
    assert stream.closed is True
