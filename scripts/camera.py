"""Camera module for BenderPi — Camera Module 3 (IMX708).

Owns the Picamera2 singleton with thread-safe reference counting.
Supports concurrent access from analyse_scene() and the MJPEG stream endpoint.

Public API
----------
acquire_camera() -> Picamera2
    Open camera on first call (ref 0 → 1); increment ref count on subsequent
    calls. Returns the shared Picamera2 instance.

release_camera()
    Decrement ref count; cancel jobs and close camera when count reaches 0.

capture_frame() -> np.ndarray
    Capture a single BGR-in-memory numpy array via capture_array("main").
    Camera must already be acquired by the caller.

opened_at() -> float | None
    Monotonic time of the last open, or None while the camera is closed.

to_pil(frame) -> PIL.Image.Image
    Convert a captured frame to an RGB PIL image.

CameraBusy
    Raised by the three calls that take _cam_lock when it stays held.

What is bounded, and what is not
--------------------------------
Two waits are bounded: a wait on _cam_lock by _LOCK_TIMEOUT_S, and a wait for
a captured frame by _CAPTURE_TIMEOUT_S.

A capture is bounded for the usual case. dispatch_functions() and
cancel_all_and_flush() take Picamera2's own lock, and that lock has no
timeout. A stuck event thread can hold it, and then one thread is stuck
while it holds _cam_lock.

Picamera2(), configure(), start() and close() have no timeout. A call that
stops while the GIL is free holds _cam_lock in that one thread for ever.
Callers then get CameraBusy after _LOCK_TIMEOUT_S, and the camera stays
unusable until bender-web restarts. The bound protects the 8-thread default
executor, which STOP BENDER needs.

A hang inside a libcamera C++ call also holds the GIL, because the binding
has no gil_scoped_release. Then the whole process stops, including the event
loop and STOP BENDER, and the lock timeouts cannot run. Recovery is SSH:
`sudo systemctl stop bender-converse`, then `sudo systemctl restart
bender-web`. No hang of this kind is on record. This binding fact was read on
the dev clone, and the device build is not verified. Whether to accept this
limit is open for the owner, before turn frames are enabled.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from logger import get_logger

log = get_logger("camera")

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_cam = None
_cam_lock = threading.Lock()
_cam_refcount = 0
_opened_at = None

# Main stream config: capture_array("main") returns H×W×3 uint8. Despite the
# name, picamera2's RGB888 is [B, G, R] in memory (picamera2/request.py).
_MAIN_CONFIG = {"format": "RGB888", "size": (1920, 1080)}

# Module constants, not cfg keys. wait=None blocks for ever and wait=0 returns
# a Job, so a cleared Config box must never be able to reach these.
_CAPTURE_TIMEOUT_S = 3.0
_LOCK_TIMEOUT_S = 5.0


class CameraBusy(RuntimeError):
    """_cam_lock stayed held for _LOCK_TIMEOUT_S. Nothing changed."""


@contextmanager
def _locked():
    """Hold _cam_lock, or raise CameraBusy after _LOCK_TIMEOUT_S."""
    if not _cam_lock.acquire(timeout=_LOCK_TIMEOUT_S):
        raise CameraBusy(f"camera lock held for over {_LOCK_TIMEOUT_S} s")
    try:
        yield
    finally:
        _cam_lock.release()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _flush_jobs(cam):
    """Cancel every queued job. Log a failure and go on.

    The callers are recovering from something worse, and their own error or
    their close() must survive this. Job.cancel raises InvalidStateError if a
    job finished between its timeout and this call.
    """
    try:
        cam.cancel_all_and_flush()
    except Exception as exc:
        log.warning("cancel_all_and_flush failed: %r", exc)


def _open_camera():
    """Instantiate and start Picamera2. Caller must hold _cam_lock."""
    global _cam, _opened_at
    try:
        from picamera2 import Picamera2  # lazy import — allows mocking in tests
    except ImportError as exc:
        raise RuntimeError(
            "picamera2 is not available. Install it or run on a Raspberry Pi."
        ) from exc

    log.info("Opening Camera Module 3 (IMX708)")
    cam = Picamera2()
    try:
        config = cam.create_video_configuration(main=_MAIN_CONFIG)
        cam.configure(config)
        cam.start()
    except Exception:
        # Picamera2 registers atexit.register(self.close), a strong ref, so an
        # unclosed instance keeps the camera for the life of the process.
        try:
            cam.close()
        except Exception as exc:
            log.warning("Error closing camera after a failed open: %r", exc)
        raise
    _cam = cam
    _opened_at = time.monotonic()
    log.info("Camera started")


def _close_camera():
    """Cancel jobs and close Picamera2. Caller must hold _cam_lock.

    Never call stop() here. It can block for ever after one exception in
    process_requests. close() runs its own stop with no timeout, which is why
    close() is on the unbounded list in the module docstring.
    """
    global _cam, _opened_at
    if _cam is None:
        return
    try:
        _flush_jobs(_cam)
        _cam.close()
        log.info("Camera closed")
    except Exception as exc:
        log.warning("Error closing camera: %s", exc)
    finally:
        _cam = None
        _opened_at = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def acquire_camera():
    """Return the shared Picamera2 instance, opening it if needed.

    Increments the internal reference count. Every call to acquire_camera()
    must be paired with a call to release_camera().

    Raises CameraBusy if _cam_lock stays held. That call took no ref, so it
    must not be paired with a release: a release would take another holder's.
    """
    global _cam_refcount
    with _locked():
        if _cam is None:
            _open_camera()
        _cam_refcount += 1
        log.debug("Camera acquired (refcount=%d)", _cam_refcount)
        return _cam


def release_camera():
    """Decrement ref count; close camera when count reaches zero.

    Raises CameraBusy if _cam_lock stays held. The ref then stays counted.
    """
    global _cam_refcount
    with _locked():
        if _cam_refcount <= 0:
            log.warning("release_camera() called with refcount already 0 — ignoring")
            return
        _cam_refcount -= 1
        log.debug("Camera released (refcount=%d)", _cam_refcount)
        if _cam_refcount == 0:
            _close_camera()


def capture_frame():
    """Capture a single frame as a numpy array, BGR in memory.

    Returns
    -------
    np.ndarray
        Shape (H, W, 3), dtype uint8, [B, G, R] channel order in memory. Use
        to_pil() to get an image with the colours right.

    Raises
    ------
    TimeoutError
        No frame in _CAPTURE_TIMEOUT_S. Every job is cancelled. The camera
        stays open for its holders, who release it as usual. The last release
        closes it, and the next acquire opens it fresh.
    CameraBusy
        _cam_lock stayed held for _LOCK_TIMEOUT_S.

    Notes
    -----
    The caller is responsible for having already called acquire_camera().

    This holds _cam_lock for the whole capture, up to _CAPTURE_TIMEOUT_S, on
    purpose. cancel_all_and_flush() is global, so one job at a time under one
    lock means a flush can only ever hit the job that timed out. Do not call
    capture_array anywhere else.
    """
    with _locked():
        if _cam is None:
            raise RuntimeError("Camera is not open. Call acquire_camera() first.")
        try:
            return _cam.capture_array("main", wait=_CAPTURE_TIMEOUT_S)
        except TimeoutError as exc:
            # Job.get_result is Future.result(timeout=...), and on Python 3.11+
            # that raises the builtin. Any other job failure is not a hang, so
            # it propagates unchanged with no flush.
            _flush_jobs(_cam)
            raise TimeoutError(f"no frame in {_CAPTURE_TIMEOUT_S} s") from exc


def opened_at():
    """Monotonic time of the last open, or None while the camera is closed.

    A second acquire does not move it, so a camera that is already open needs
    no settle wait. Reads one value, so it never waits for _cam_lock.
    """
    return _opened_at


def to_pil(frame):
    """Convert a captured frame to an RGB PIL image.

    The only place that knows the channel order. `frame` must be a contiguous
    buffer, which make_array gives. Do not copy or slice it first. This form
    measured a 6.2 ms worst GIL stall, against 56.6 ms for
    `fromarray(arr[..., ::-1])`.
    """
    from PIL import Image  # lazy: bender-converse imports this module

    height, width = frame.shape[:2]
    return Image.frombuffer("RGB", (width, height), frame, "raw", "BGR", 0, 1)
