"""Unit tests for scripts/camera.py — Picamera2 singleton management.

All hardware interaction is mocked; these tests run offline.
"""
from __future__ import annotations

import importlib
import sys
import threading
import types
import unittest.mock as mock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_camera_module(mock_picamera2_cls=None):
    """Import camera.py with picamera2 mocked out, returning a fresh module."""
    # Remove any cached copy so we get a clean module state each time.
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("camera",) or mod_name.startswith("camera."):
            del sys.modules[mod_name]

    # Build a stub picamera2 package.
    pc2_mod = types.ModuleType("picamera2")
    if mock_picamera2_cls is None:
        mock_picamera2_cls = _default_mock_picamera2()
    pc2_mod.Picamera2 = mock_picamera2_cls
    sys.modules["picamera2"] = pc2_mod

    import camera
    importlib.reload(camera)
    return camera


def _default_mock_picamera2():
    """Return a MagicMock class that behaves like Picamera2."""
    instance = mock.MagicMock()
    instance.capture_array.return_value = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cls = mock.MagicMock(return_value=instance)
    cls._instance = instance  # expose for assertions
    return cls


# ---------------------------------------------------------------------------
# acquire_camera tests
# ---------------------------------------------------------------------------

def test_acquire_camera_opens_on_first_call():
    """First acquire should instantiate Picamera2 and start it."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    result = cam_mod.acquire_camera()

    mock_cls.assert_called_once()
    mock_cls._instance.configure.assert_called_once()
    mock_cls._instance.start.assert_called_once()
    assert result is mock_cls._instance


def test_acquire_camera_increments_refcount_without_reopening():
    """Second acquire should NOT call Picamera2() again — just bump refcount."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    cam_mod.acquire_camera()
    cam_mod.acquire_camera()

    # Constructor called exactly once
    assert mock_cls.call_count == 1
    assert cam_mod._cam_refcount == 2


def test_acquire_camera_returns_same_instance_on_second_call():
    """Both calls must return the same Picamera2 instance."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    first = cam_mod.acquire_camera()
    second = cam_mod.acquire_camera()

    assert first is second


# ---------------------------------------------------------------------------
# release_camera tests
# ---------------------------------------------------------------------------

def test_release_camera_closes_at_zero_refcount():
    """After acquiring once, releasing should stop + close the camera."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    cam_mod.acquire_camera()
    cam_mod.release_camera()

    mock_cls._instance.stop.assert_called_once()
    mock_cls._instance.close.assert_called_once()
    assert cam_mod._cam is None
    assert cam_mod._cam_refcount == 0


def test_release_camera_does_not_close_when_refcount_still_positive():
    """With two acquires, first release should NOT close the camera."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    cam_mod.acquire_camera()
    cam_mod.acquire_camera()
    cam_mod.release_camera()

    mock_cls._instance.stop.assert_not_called()
    mock_cls._instance.close.assert_not_called()
    assert cam_mod._cam_refcount == 1
    assert cam_mod._cam is not None


def test_release_camera_second_release_closes():
    """Second release (refcount → 0) must close."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    cam_mod.acquire_camera()
    cam_mod.acquire_camera()
    cam_mod.release_camera()
    cam_mod.release_camera()

    mock_cls._instance.stop.assert_called_once()
    mock_cls._instance.close.assert_called_once()
    assert cam_mod._cam is None


# ---------------------------------------------------------------------------
# capture_frame tests
# ---------------------------------------------------------------------------

def test_capture_frame_returns_numpy_array():
    """capture_frame() should return a numpy array."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    cam_mod.acquire_camera()
    frame = cam_mod.capture_frame()

    assert isinstance(frame, np.ndarray)
    mock_cls._instance.capture_array.assert_called_once_with("main")
    cam_mod.release_camera()


# ---------------------------------------------------------------------------
# Thread-safety test
# ---------------------------------------------------------------------------

def test_thread_safety_concurrent_acquire_release():
    """Concurrent acquire/release from many threads should leave refcount at 0."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    errors = []

    def worker():
        try:
            cam_mod.acquire_camera()
            # small yield to maximise interleaving
            import time; time.sleep(0.001)
            cam_mod.release_camera()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
    assert cam_mod._cam_refcount == 0


# ---------------------------------------------------------------------------
# Missing picamera2 raises RuntimeError
# ---------------------------------------------------------------------------

def test_acquire_raises_runtimeerror_when_picamera2_unavailable():
    # If picamera2 cannot be imported, a clear RuntimeError should be raised.
    # Install a sentinel that raises ImportError when picamera2 is accessed.
    import builtins

    real_import = builtins.__import__

    def _failing_import(name, *args, **kwargs):
        if name == 'picamera2':
            raise ImportError('No module named picamera2')
        return real_import(name, *args, **kwargs)

    for mod_name in list(sys.modules.keys()):
        if mod_name in ('camera',) or mod_name.startswith('camera.'):
            del sys.modules[mod_name]
    sys.modules.pop('picamera2', None)

    with mock.patch('builtins.__import__', side_effect=_failing_import):
        import camera
        importlib.reload(camera)
        with pytest.raises(RuntimeError, match='[Pp]icamera2'):
            camera.acquire_camera()
