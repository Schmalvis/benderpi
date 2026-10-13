"""Camera module for BenderPi — Camera Module 3 (IMX708).

Owns the Picamera2 singleton with thread-safe reference counting.
Supports concurrent access from analyse_scene() and the MJPEG stream endpoint.

Public API
----------
acquire_camera() -> Picamera2
    Open camera on first call (ref 0 → 1); increment ref count on subsequent
    calls. Returns the shared Picamera2 instance.

release_camera()
    Decrement ref count; stop and close camera when count reaches 0.

capture_frame() -> np.ndarray
    Capture a single RGB numpy array via capture_array("main").
    Camera must already be acquired by the caller.
"""
from __future__ import annotations

import threading

from logger import get_logger

log = get_logger("camera")

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_cam = None
_cam_lock = threading.Lock()
_cam_refcount = 0

# Main stream config: RGB888 → capture_array("main") returns H×W×3 uint8
_MAIN_CONFIG = {"format": "RGB888", "size": (1920, 1080)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _open_camera():
    """Instantiate and start Picamera2. Caller must hold _cam_lock."""
    global _cam
    try:
        from picamera2 import Picamera2  # lazy import — allows mocking in tests
    except ImportError as exc:
        raise RuntimeError(
            "picamera2 is not available. Install it or run on a Raspberry Pi."
        ) from exc

    log.info("Opening Camera Module 3 (IMX708)")
    cam = Picamera2()
    config = cam.create_video_configuration(main=_MAIN_CONFIG)
    cam.configure(config)
    try:
        cam.start()
    except Exception:
        cam.close()
        raise
    _cam = cam
    log.info("Camera started")


def _close_camera():
    """Stop and close Picamera2. Caller must hold _cam_lock."""
    global _cam
    if _cam is None:
        return
    try:
        _cam.stop()
        _cam.close()
        log.info("Camera stopped and closed")
    except Exception as exc:
        log.warning("Error closing camera: %s", exc)
    finally:
        _cam = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def acquire_camera():
    """Return the shared Picamera2 instance, opening it if needed.

    Increments the internal reference count. Every call to acquire_camera()
    must be paired with a call to release_camera().
    """
    global _cam_refcount
    with _cam_lock:
        if _cam is None:
            _open_camera()
        _cam_refcount += 1
        log.debug("Camera acquired (refcount=%d)", _cam_refcount)
        return _cam


def release_camera():
    """Decrement ref count; close camera when count reaches zero."""
    global _cam_refcount
    with _cam_lock:
        if _cam_refcount <= 0:
            log.warning("release_camera() called with refcount already 0 — ignoring")
            return
        _cam_refcount -= 1
        log.debug("Camera released (refcount=%d)", _cam_refcount)
        if _cam_refcount == 0:
            _close_camera()


def capture_frame():
    """Capture a single frame as an RGB numpy array.

    Returns
    -------
    np.ndarray
        Shape (H, W, 3), dtype uint8, RGB channel order.

    Notes
    -----
    The caller is responsible for having already called acquire_camera().
    """
    with _cam_lock:
        if _cam is None:
            raise RuntimeError("Camera is not open. Call acquire_camera() first.")
        return _cam.capture_array("main")
