"""Unit tests for scripts/camera.py — Picamera2 singleton management.

All hardware interaction is mocked; these tests run offline.
"""
from __future__ import annotations

import concurrent.futures
import importlib
import subprocess
import sys
import threading
import time
import types
import unittest.mock as mock
from pathlib import Path

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
    """After acquiring once, releasing should cancel jobs and close the camera."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    cam_mod.acquire_camera()
    cam_mod.release_camera()

    mock_cls._instance.cancel_all_and_flush.assert_called_once()
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

    mock_cls._instance.cancel_all_and_flush.assert_not_called()
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

    mock_cls._instance.cancel_all_and_flush.assert_called_once()
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
    cam_mod.release_camera()


def test_capture_frame_passes_the_bound():
    """`wait=None` blocks for ever, and `wait=0` returns a Job, not a frame."""
    mock_cls = _default_mock_picamera2()
    cam_mod = _make_camera_module(mock_cls)

    cam_mod.acquire_camera()
    cam_mod.capture_frame()

    mock_cls._instance.capture_array.assert_called_once_with(
        "main", wait=cam_mod._CAPTURE_TIMEOUT_S)
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


# ---------------------------------------------------------------------------
# Hang recovery
# ---------------------------------------------------------------------------
#
# A dead sensor or a loose ribbon leaves picamera2's event loop running with no
# frames to finish a job. `wait=None` then blocks for ever under _cam_lock, and
# every later caller blocks behind it on the 8-thread pool that STOP BENDER
# needs. These tests cover the recovery path. Plan: 2026-10-31-turn-frames-plan.

def _time_out(name="main", wait=None):
    """The real failure. Job.get_result is Future.result(timeout=wait)."""
    return concurrent.futures.Future().result(timeout=0.01)


def _opened(cam_mod, refs=1):
    for _ in range(refs):
        cam_mod.acquire_camera()


class TestConstants:
    def test_the_bounds_are_module_constants(self):
        cam_mod = _make_camera_module()
        assert cam_mod._CAPTURE_TIMEOUT_S == 3.0
        assert cam_mod._LOCK_TIMEOUT_S == 5.0

    def test_one_hung_capture_cannot_time_out_the_next_lock_wait(self):
        """So a stream's release, waiting behind its own capture, still gets in."""
        cam_mod = _make_camera_module()
        assert cam_mod._CAPTURE_TIMEOUT_S < cam_mod._LOCK_TIMEOUT_S

    def test_the_bounds_do_not_come_from_cfg(self):
        """A cleared Config box must never turn a bound into `wait=None`."""
        cam_mod = _make_camera_module()
        assert not hasattr(cam_mod, "cfg")


class TestCaptureTimeout:
    def test_the_python_fact_this_relies_on(self):
        """On 3.13 the future's timeout is the builtin. `except TimeoutError` needs this."""
        assert concurrent.futures.TimeoutError is TimeoutError

    def test_a_timeout_flushes_and_raises(self):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.capture_array.side_effect = _time_out
        _opened(cam_mod)

        with pytest.raises(TimeoutError):
            cam_mod.capture_frame()

        mock_cls._instance.cancel_all_and_flush.assert_called_once()

    def test_the_timeout_says_what_happened(self):
        """Future.result raises a bare TimeoutError. `str()` of that is empty,
        and analyse_scene and the outcome log both print `str(exc)`."""
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.capture_array.side_effect = _time_out
        _opened(cam_mod)

        with pytest.raises(TimeoutError) as excinfo:
            cam_mod.capture_frame()

        assert str(excinfo.value)

    def test_a_timeout_leaves_the_camera_for_the_holders_to_release(self):
        """Another holder may still have a ref, so capture must not close it."""
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.capture_array.side_effect = _time_out
        _opened(cam_mod, refs=2)

        with pytest.raises(TimeoutError):
            cam_mod.capture_frame()

        mock_cls._instance.close.assert_not_called()
        assert cam_mod._cam_refcount == 2

    def test_the_camera_closes_and_reopens_after_a_timeout(self):
        first, second = mock.MagicMock(), mock.MagicMock()
        first.capture_array.side_effect = _time_out
        second.capture_array.return_value = np.zeros((4, 4, 3), dtype=np.uint8)
        mock_cls = mock.MagicMock(side_effect=[first, second])
        cam_mod = _make_camera_module(mock_cls)
        _opened(cam_mod)

        with pytest.raises(TimeoutError):
            cam_mod.capture_frame()
        cam_mod.release_camera()

        first.close.assert_called_once()
        assert cam_mod._cam is None
        assert cam_mod._cam_refcount == 0

        assert cam_mod.acquire_camera() is second
        assert cam_mod.capture_frame().shape == (4, 4, 3)
        cam_mod.release_camera()

    def test_a_timeout_frees_the_lock(self):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.capture_array.side_effect = _time_out
        _opened(cam_mod)

        with pytest.raises(TimeoutError):
            cam_mod.capture_frame()

        assert not cam_mod._cam_lock.locked()

    @pytest.mark.parametrize("error", [
        RuntimeError("the sensor said no"),
        ValueError("bad stream name"),
        concurrent.futures.CancelledError(),
    ], ids=["runtime", "value", "cancelled"])
    def test_any_other_failure_propagates_unchanged_and_does_not_flush(self, error):
        """cancel_all_and_flush is global. Only a timeout has earned that."""
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.capture_array.side_effect = error
        _opened(cam_mod)

        with pytest.raises(type(error)) as excinfo:
            cam_mod.capture_frame()

        assert excinfo.value is error
        mock_cls._instance.cancel_all_and_flush.assert_not_called()

    def test_a_raising_flush_does_not_mask_the_timeout(self):
        """Job.cancel raises InvalidStateError if the job finished in the gap."""
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.capture_array.side_effect = _time_out
        mock_cls._instance.cancel_all_and_flush.side_effect = (
            concurrent.futures.InvalidStateError("already finished"))
        _opened(cam_mod)

        with pytest.raises(TimeoutError):
            cam_mod.capture_frame()


class TestOneJobAtATime:
    def test_capture_holds_the_lock_for_the_capture(self):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        seen = []

        def capture(name, wait):
            seen.append(cam_mod._cam_lock.locked())
            return np.zeros((2, 2, 3), dtype=np.uint8)

        mock_cls._instance.capture_array.side_effect = capture
        _opened(cam_mod)
        cam_mod.capture_frame()

        assert seen == [True]

    def test_two_captures_never_overlap(self):
        """One event-loop job at a time, so a flush can only hit its own job."""
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        running = []
        overlaps = []

        def capture(name, wait):
            running.append(1)
            if len(running) > 1:
                overlaps.append(1)
            time.sleep(0.005)
            running.pop()
            return np.zeros((2, 2, 3), dtype=np.uint8)

        mock_cls._instance.capture_array.side_effect = capture
        _opened(cam_mod)
        threads = [threading.Thread(target=cam_mod.capture_frame) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert overlaps == []

    def test_there_is_exactly_one_lock(self):
        cam_mod = _make_camera_module()
        lock_types = (type(threading.Lock()), type(threading.RLock()))
        locks = [v for v in vars(cam_mod).values() if isinstance(v, lock_types)]
        assert locks == [cam_mod._cam_lock]

    def test_a_hung_capture_makes_others_busy_not_stuck(self, monkeypatch):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        monkeypatch.setattr(cam_mod, "_LOCK_TIMEOUT_S", 0.05)
        inside, finish = threading.Event(), threading.Event()

        def capture(name, wait):
            inside.set()
            finish.wait(5)
            return np.zeros((2, 2, 3), dtype=np.uint8)

        mock_cls._instance.capture_array.side_effect = capture
        _opened(cam_mod)
        holder = threading.Thread(target=cam_mod.capture_frame)
        holder.start()
        try:
            assert inside.wait(5)
            with pytest.raises(cam_mod.CameraBusy):
                cam_mod.capture_frame()
        finally:
            finish.set()
            holder.join()

    def test_capture_without_acquire_still_raises(self):
        cam_mod = _make_camera_module()
        with pytest.raises(RuntimeError, match="not open"):
            cam_mod.capture_frame()


class TestClose:
    def test_close_cancels_then_closes(self):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        _opened(cam_mod)
        mock_cls._instance.reset_mock()

        cam_mod.release_camera()

        names = [call[0] for call in mock_cls._instance.method_calls]
        assert names == ["cancel_all_and_flush", "close"]

    def test_close_never_calls_stop(self):
        """stop() can block for ever after one exception in process_requests."""
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        _opened(cam_mod)

        cam_mod.release_camera()

        mock_cls._instance.stop.assert_not_called()

    def test_a_raising_flush_does_not_stop_the_close(self):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.cancel_all_and_flush.side_effect = RuntimeError("flush")
        _opened(cam_mod)

        cam_mod.release_camera()

        mock_cls._instance.close.assert_called_once()
        assert cam_mod._cam is None
        assert cam_mod.opened_at() is None

    def test_a_raising_close_still_clears_the_state(self):
        """A stale `_cam` would hand the next caller a dead camera."""
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.close.side_effect = RuntimeError("close")
        _opened(cam_mod)

        cam_mod.release_camera()

        assert cam_mod._cam is None
        assert cam_mod.opened_at() is None
        assert cam_mod._cam_refcount == 0

    def test_both_raising_still_clears_the_state(self):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.cancel_all_and_flush.side_effect = RuntimeError("flush")
        mock_cls._instance.close.side_effect = RuntimeError("close")
        _opened(cam_mod)

        cam_mod.release_camera()

        assert cam_mod._cam is None
        assert cam_mod.opened_at() is None


class TestFailedOpen:
    @pytest.mark.parametrize("step", ["create_video_configuration", "configure", "start"])
    def test_a_failed_step_closes_the_camera(self, step):
        """Picamera2's atexit hook keeps a strong ref, so a leak is a held camera."""
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        getattr(mock_cls._instance, step).side_effect = RuntimeError(step)

        with pytest.raises(RuntimeError, match=step):
            cam_mod.acquire_camera()

        mock_cls._instance.close.assert_called_once()
        assert cam_mod._cam is None
        assert cam_mod._cam_refcount == 0
        assert cam_mod.opened_at() is None

    def test_a_raising_close_keeps_the_real_error(self):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        mock_cls._instance.configure.side_effect = RuntimeError("configure")
        mock_cls._instance.close.side_effect = ValueError("close")

        with pytest.raises(RuntimeError, match="configure"):
            cam_mod.acquire_camera()

    def test_the_next_acquire_works_after_a_failed_open(self):
        bad, good = mock.MagicMock(), mock.MagicMock()
        bad.configure.side_effect = RuntimeError("configure")
        mock_cls = mock.MagicMock(side_effect=[bad, good])
        cam_mod = _make_camera_module(mock_cls)

        with pytest.raises(RuntimeError):
            cam_mod.acquire_camera()

        assert cam_mod.acquire_camera() is good
        assert cam_mod._cam_refcount == 1


class TestLockTimeout:
    @pytest.mark.parametrize("call", ["acquire_camera", "release_camera", "capture_frame"])
    def test_a_held_lock_raises_camera_busy_within_the_timeout(self, call, monkeypatch):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        monkeypatch.setattr(cam_mod, "_LOCK_TIMEOUT_S", 0.05)
        _opened(cam_mod)

        assert cam_mod._cam_lock.acquire(timeout=1)
        started = time.monotonic()
        try:
            with pytest.raises(cam_mod.CameraBusy):
                getattr(cam_mod, call)()
        finally:
            cam_mod._cam_lock.release()

        assert time.monotonic() - started < 1.0

    @pytest.mark.parametrize("call", ["acquire_camera", "release_camera", "capture_frame"])
    def test_a_busy_call_changes_nothing(self, call, monkeypatch):
        mock_cls = _default_mock_picamera2()
        cam_mod = _make_camera_module(mock_cls)
        monkeypatch.setattr(cam_mod, "_LOCK_TIMEOUT_S", 0.01)
        _opened(cam_mod)
        mock_cls._instance.reset_mock()

        assert cam_mod._cam_lock.acquire(timeout=1)
        try:
            with pytest.raises(cam_mod.CameraBusy):
                getattr(cam_mod, call)()
        finally:
            cam_mod._cam_lock.release()

        assert cam_mod._cam_refcount == 1
        assert mock_cls._instance.method_calls == []

    def test_a_busy_call_does_not_release_a_lock_it_never_held(self, monkeypatch):
        cam_mod = _make_camera_module()
        monkeypatch.setattr(cam_mod, "_LOCK_TIMEOUT_S", 0.01)

        assert cam_mod._cam_lock.acquire(timeout=1)
        try:
            with pytest.raises(cam_mod.CameraBusy):
                cam_mod.acquire_camera()
            assert cam_mod._cam_lock.locked()
        finally:
            cam_mod._cam_lock.release()

    def test_the_camera_works_once_the_lock_is_free(self, monkeypatch):
        cam_mod = _make_camera_module()
        monkeypatch.setattr(cam_mod, "_LOCK_TIMEOUT_S", 0.01)

        assert cam_mod._cam_lock.acquire(timeout=1)
        with pytest.raises(cam_mod.CameraBusy):
            cam_mod.acquire_camera()
        cam_mod._cam_lock.release()

        cam_mod.acquire_camera()
        assert cam_mod._cam_refcount == 1


class TestOpenedAt:
    def test_none_before_any_open(self):
        cam_mod = _make_camera_module()
        assert cam_mod.opened_at() is None

    def test_set_by_an_open(self):
        cam_mod = _make_camera_module()
        before = time.monotonic()
        _opened(cam_mod)
        assert before <= cam_mod.opened_at() <= time.monotonic()

    def test_a_second_acquire_does_not_move_it(self):
        """See Room holds the camera open, so a frame then needs no settle wait."""
        cam_mod = _make_camera_module()
        _opened(cam_mod)
        first = cam_mod.opened_at()

        cam_mod.acquire_camera()
        cam_mod.capture_frame()
        cam_mod.release_camera()

        assert cam_mod.opened_at() == first

    def test_a_reopen_moves_it(self):
        cam_mod = _make_camera_module()
        _opened(cam_mod)
        first = cam_mod.opened_at()
        cam_mod.release_camera()
        _opened(cam_mod)

        assert cam_mod.opened_at() > first

    def test_none_after_the_close(self):
        cam_mod = _make_camera_module()
        _opened(cam_mod)
        cam_mod.release_camera()
        assert cam_mod.opened_at() is None

    def test_does_not_wait_for_the_lock(self):
        """It reads one number. A stuck open must not stall it for 5 s."""
        cam_mod = _make_camera_module()
        assert cam_mod._cam_lock.acquire(timeout=1)
        try:
            started = time.monotonic()
            assert cam_mod.opened_at() is None
            assert time.monotonic() - started < 1.0
        finally:
            cam_mod._cam_lock.release()


class TestToPil:
    def test_pure_blue_stays_blue(self):
        """RGB888 is [B, G, R] in memory. This fails if the channel order flips."""
        cam_mod = _make_camera_module()
        frame = np.zeros((2, 2, 3), dtype=np.uint8)
        frame[..., 0] = 255

        assert cam_mod.to_pil(frame).getpixel((0, 0)) == (0, 0, 255)

    def test_every_channel_lands_in_its_place(self):
        cam_mod = _make_camera_module()
        frame = np.array([[[10, 20, 30]]], dtype=np.uint8)
        assert cam_mod.to_pil(frame).getpixel((0, 0)) == (30, 20, 10)

    def test_width_and_height_are_not_swapped(self):
        cam_mod = _make_camera_module()
        frame = np.zeros((4, 6, 3), dtype=np.uint8)
        frame[3, 5] = (255, 0, 0)

        image = cam_mod.to_pil(frame)

        assert image.size == (6, 4)
        assert image.mode == "RGB"
        assert image.getpixel((5, 3)) == (0, 0, 255)

    def test_it_does_not_copy_or_slice_the_array(self):
        """`fromarray(arr[..., ::-1])` measured a 56.6 ms GIL stall. Pillow reads
        the contiguous buffer that make_array returns, so no Python copy is due."""
        cam_mod = _make_camera_module()

        class NoCopy(np.ndarray):
            def copy(self, *a, **k):
                raise AssertionError("to_pil copied the frame")

            def tobytes(self, *a, **k):
                raise AssertionError("to_pil copied the frame")

            def __getitem__(self, key):
                raise AssertionError("to_pil sliced the frame")

        frame = np.zeros((2, 2, 3), dtype=np.uint8).view(NoCopy)

        assert cam_mod.to_pil(frame).size == (2, 2)

    def test_importing_camera_does_not_import_pil(self):
        """bender-converse must not pay for PIL through this module."""
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        code = (
            "import sys; sys.path.insert(0, %r); import camera; "
            "sys.exit('PIL' in sys.modules)" % str(scripts)
        )
        assert subprocess.run([sys.executable, "-c", code], timeout=30).returncode == 0
