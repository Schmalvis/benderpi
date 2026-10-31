"""Tests for scripts/turn_frames.py.

The module holds a daemon thread, a file queue shared by two processes and a
held camera, so the tests are about failure modes first. Almost everything
drives `_Worker.run_pass()` directly with no thread. A few tests run the real
thread with a short patched poll.
"""
import datetime
import json
import os
import stat
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

import camera
import config
import turn_frames

CameraBusy = camera.CameraBusy
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StepStop:
    """Stands in for the worker's stop Event, with no real waiting.

    `is_set()` is False for `passes` calls. `wait()` records its timeout and
    returns False at once, so the settle wait and the poll sleep cost nothing.
    """

    def __init__(self, passes=10 ** 9):
        self.passes = passes
        self.waits = []

    def is_set(self):
        if self.passes <= 0:
            return True
        self.passes -= 1
        return False

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return False

    def set(self):
        self.passes = 0


class FakeCamera:
    """The seven names turn_frames uses from the camera module."""

    CameraBusy = CameraBusy

    def __init__(self):
        self.events = []
        self.refs = 0
        self.open_t = None
        self.acquire_error = None
        self.capture_error = None
        self.release_error = None
        self.queue_dir_at_capture = None
        self.on_capture = None
        self.frame_bgr = (200, 30, 20)  # B, G, R. Real colour is R=20 G=30 B=200.
        self.frame_shape = (72, 128)
        self.opened_at_value = "auto"

    def acquire_camera(self):
        self.events.append("acquire")
        if self.acquire_error is not None:
            raise self.acquire_error
        if self.refs == 0:
            self.open_t = time.monotonic()
        self.refs += 1
        return object()

    def release_camera(self):
        self.events.append("release")
        if self.release_error is not None:
            raise self.release_error
        if self.refs > 0:
            self.refs -= 1
            if self.refs == 0:
                self.open_t = None

    def capture_frame(self):
        self.events.append("capture")
        if self.on_capture is not None:
            self.on_capture()
        if self.capture_error is not None:
            raise self.capture_error
        h, w = self.frame_shape
        frame = np.empty((h, w, 3), dtype=np.uint8)
        frame[:, :] = self.frame_bgr
        return frame

    def opened_at(self):
        if self.opened_at_value != "auto":
            return self.opened_at_value
        return self.open_t

    to_pil = staticmethod(camera.to_pil)


def frames_root():
    return turn_frames.frames_dir()


def queue_dir():
    return os.path.join(frames_root(), ".queue")


def today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def make_note(name, day=None, age=0.0, filename=None):
    """Create a note in the queue with mtime = now - age."""
    q = queue_dir()
    os.makedirs(q, exist_ok=True)
    fname = filename if filename is not None else f"{day or today()}__{name}"
    path = os.path.join(q, fname)
    t = time.time() - age
    # Build the note outside the queue and rename it in. A live worker polling
    # the queue must never see a file whose mtime is not yet set, or remove it
    # between create and utime.
    tmp = os.path.join(os.path.dirname(q), ".make_note_" + fname)
    with open(tmp, "w") as f:
        os.utime(f.fileno(), (t, t))
    os.rename(tmp, path)
    return path


def outcomes(day=None):
    path = os.path.join(frames_root(), day or today(), "outcomes.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def by_name(rows):
    return {r["name"]: r for r in rows}


def run(worker, stop=None, startup=False):
    worker.run_pass(stop or StepStop(), startup=startup)


def write_config(tmp_path, data):
    path = tmp_path / "bender_config.json"
    if isinstance(data, str):
        path.write_text(data)
    else:
        path.write_text(json.dumps(data))
    return path


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def mode_of(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def all_files(root):
    out = []
    for d, dirs, files in os.walk(root):
        out.extend(os.path.join(d, n) for n in dirs + files)
    return out


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Every test writes under tmp_path, and none can see the real config."""
    logdir = tmp_path / "logs"
    logdir.mkdir()
    monkeypatch.setenv("BENDER_LOG_DIR", str(logdir))
    monkeypatch.setattr(config, "_DEFAULT_CONFIG_PATH",
                        str(tmp_path / "bender_config.json"))
    monkeypatch.setattr(turn_frames.cfg, "turn_frames_enabled", False,
                        raising=False)
    monkeypatch.setattr(turn_frames.cfg, "turn_frames_retention_days", 7,
                        raising=False)
    monkeypatch.setattr(turn_frames.cfg, "turn_frames_settle_s", 0.5,
                        raising=False)
    monkeypatch.setattr(turn_frames, "_current", None)
    monkeypatch.setattr(turn_frames, "_last_logged", {})
    os.umask(0o022)
    yield
    turn_frames.stop_worker()


@pytest.fixture
def cam(monkeypatch):
    fake = FakeCamera()
    monkeypatch.setattr(turn_frames, "camera", fake)
    return fake


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(turn_frames.cfg, "turn_frames_enabled", True)


@pytest.fixture
def worker():
    return turn_frames._Worker()


# ---------------------------------------------------------------------------
# enabled(), off by default
# ---------------------------------------------------------------------------

class TestEnabled:
    def test_true_only_for_literal_true(self, monkeypatch):
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_enabled", True)
        assert turn_frames.enabled() is True

    @pytest.mark.parametrize("value", ["true", "yes", 1, 0, None, False, [],
                                       MagicMock()])
    def test_anything_else_is_false(self, monkeypatch, value):
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_enabled", value)
        assert turn_frames.enabled() is False

    def test_default_config_is_off(self):
        assert config.Config().turn_frames_enabled is False

    def test_off_request_makes_no_note_and_no_folder(self):
        assert turn_frames.request("abc_t01") is None
        assert not os.path.exists(frames_root())

    def test_off_warm_creates_nothing(self):
        turn_frames.warm()
        assert not os.path.exists(frames_root())

    def test_warm_never_raises(self, on, monkeypatch):
        monkeypatch.setattr(turn_frames, "_ensure_queue",
                            MagicMock(side_effect=OSError("boom")))
        turn_frames.warm()
        assert turn_frames.request("a") is None


# ---------------------------------------------------------------------------
# request() and warm()
# ---------------------------------------------------------------------------

class TestRequest:
    def test_returns_relative_path_and_makes_one_note(self, on):
        path = turn_frames.request("s1_t01")
        assert path == f"{today()}/s1_t01.jpg"
        assert os.listdir(queue_dir()) == [f"{today()}__s1_t01"]

    def test_note_is_empty_and_0600(self, on):
        turn_frames.request("s1_t01")
        note = os.path.join(queue_dir(), f"{today()}__s1_t01")
        assert os.path.getsize(note) == 0
        assert mode_of(note) == 0o600

    @pytest.mark.parametrize("name", [
        "", "a/b", "../x", "..\\x", "x\n", "a b", "café", "a" * 65, None,
        5, b"abc", "a\x00b", "a:b", "a\tb", "١٢",
    ])
    def test_refuses_a_bad_name(self, on, name):
        assert turn_frames.request(name) is None
        if os.path.exists(queue_dir()):
            assert os.listdir(queue_dir()) == []

    @pytest.mark.parametrize("name", ["a", "a" * 64, "Sess-1.x_t09", "..", "."])
    def test_accepts_a_legal_name(self, on, name):
        assert turn_frames.request(name) == f"{today()}/{name}.jpg"

    def test_stays_inside_frames_dir(self, on, tmp_path):
        turn_frames.request("ok")
        for bad in ("../escape", "a/../../escape", "/abs"):
            assert turn_frames.request(bad) is None
        logs = str(tmp_path / "logs")
        outside = [p for p in all_files(str(tmp_path))
                   if p != logs and not p.startswith(frames_root())]
        assert outside == []

    def test_skips_at_32_waiting_notes(self, on):
        for i in range(31):
            make_note(f"n{i}")
        assert turn_frames.request("last") is not None
        assert len(os.listdir(queue_dir())) == 32
        assert turn_frames.request("one_too_many") is None
        assert len(os.listdir(queue_dir())) == 32

    def test_the_warm_note_counts_toward_the_limit(self, on):
        for i in range(31):
            make_note(f"n{i}")
        turn_frames.warm()
        assert turn_frames.request("x") is None

    def test_creates_missing_folders_at_0700(self, on):
        assert not os.path.exists(frames_root())
        turn_frames.request("a")
        assert mode_of(frames_root()) == 0o700
        assert mode_of(queue_dir()) == 0o700

    def test_the_date_is_local_and_taken_at_request_time(self, on, monkeypatch):
        real = turn_frames.datetime

        class Fixed(real.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2031, 1, 2, 3, 4, 5)

        fake = MagicMock(wraps=real)
        fake.datetime = Fixed
        monkeypatch.setattr(turn_frames, "datetime", fake)
        assert turn_frames.request("a") == "2031-01-02/a.jpg"
        assert os.listdir(queue_dir()) == ["2031-01-02__a"]

    def test_a_duplicate_name_makes_no_second_note(self, on):
        assert turn_frames.request("a") is not None
        assert turn_frames.request("a") is None
        assert len(os.listdir(queue_dir())) == 1


class TestMkdirPrivate:
    """M1: the worker walks the folders 5 times a second, so no idle chmod."""

    def test_a_folder_at_0700_gets_no_chmod(self, tmp_path, monkeypatch):
        d = tmp_path / "d"
        d.mkdir(mode=0o700)
        os.chmod(d, 0o700)
        chmod = MagicMock(wraps=os.chmod)
        monkeypatch.setattr(turn_frames.os, "chmod", chmod)
        turn_frames._mkdir_private(str(d))
        turn_frames._mkdir_private(str(d))
        chmod.assert_not_called()

    def test_a_new_folder_is_0700_under_a_permissive_umask(self, tmp_path):
        os.umask(0o000)
        try:
            turn_frames._mkdir_private(str(tmp_path / "new"))
        finally:
            os.umask(0o022)
        assert mode_of(str(tmp_path / "new")) == 0o700

    def test_a_folder_at_0755_is_fixed_to_0700(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        os.chmod(d, 0o755)
        turn_frames._mkdir_private(str(d))
        assert mode_of(str(d)) == 0o700

    def test_an_idle_pass_makes_no_chmod(self, cam, worker, monkeypatch):
        run(worker)  # builds the folders
        chmod = MagicMock(wraps=os.chmod)
        monkeypatch.setattr(turn_frames.os, "chmod", chmod)
        for _ in range(5):
            run(worker)
        chmod.assert_not_called()


class TestWarm:
    def test_makes_the_fixed_warm_note(self, on):
        turn_frames.warm()
        assert os.listdir(queue_dir()) == ["warm"]
        assert mode_of(os.path.join(queue_dir(), "warm")) == 0o600

    def test_repeated_warm_holds_one_file(self, on):
        for _ in range(20):
            turn_frames.warm()
        assert os.listdir(queue_dir()) == ["warm"]

    def test_repeated_warm_refreshes_mtime(self, on):
        turn_frames.warm()
        note = os.path.join(queue_dir(), "warm")
        os.utime(note, (1000, 1000))
        turn_frames.warm()
        assert abs(os.stat(note).st_mtime - time.time()) < 5

    def test_the_name_cannot_collide_with_a_frame_note(self):
        assert "__" not in "warm"
        assert turn_frames._parse_note("warm") is None

    def test_creates_missing_folders(self, on):
        turn_frames.warm()
        assert mode_of(frames_root()) == 0o700


# ---------------------------------------------------------------------------
# Worker pass: classification
# ---------------------------------------------------------------------------

class TestClassify:
    def test_one_fresh_note_is_saved(self, cam, worker):
        make_note("a")
        run(worker)
        rows = outcomes()
        assert [(r["name"], r["outcome"]) for r in rows] == [("a", "saved")]
        assert os.path.exists(os.path.join(frames_root(), today(), "a.jpg"))

    def test_newest_by_mtime_wins_and_the_rest_are_dropped(self, cam, worker):
        make_note("old", age=2.0)
        make_note("mid", age=1.0)
        make_note("new", age=0.1)
        run(worker)
        rows = by_name(outcomes())
        assert rows["new"]["outcome"] == "saved"
        assert rows["old"]["outcome"] == "dropped"
        assert rows["mid"]["outcome"] == "dropped"
        assert cam.events.count("capture") == 1
        assert not os.path.exists(os.path.join(frames_root(), today(), "old.jpg"))

    def test_a_tie_on_mtime_breaks_on_the_name(self, cam, worker):
        a = make_note("a", age=0.5)
        b = make_note("b", age=0.5)
        t = os.stat(a).st_mtime_ns
        os.utime(a, ns=(t, t))
        os.utime(b, ns=(t, t))
        run(worker)
        rows = by_name(outcomes())
        assert rows["b"]["outcome"] == "saved"
        assert rows["a"]["outcome"] == "dropped"

    def test_a_note_over_5s_old_is_stale(self, cam, worker):
        make_note("late", age=6.0)
        run(worker)
        assert outcomes()[0]["outcome"] == "stale"
        assert cam.events == []
        assert not os.path.exists(os.path.join(frames_root(), today(), "late.jpg"))

    def test_a_note_dated_in_the_future_is_stale_too(self, cam, worker):
        make_note("future", age=-100.0)
        run(worker)
        assert outcomes()[0]["outcome"] == "stale"
        assert cam.events == []

    def test_a_stale_note_does_not_hide_a_fresh_one(self, cam, worker):
        make_note("late", age=9.0)
        make_note("now", age=0.0)
        run(worker)
        rows = by_name(outcomes())
        assert rows["late"]["outcome"] == "stale"
        assert rows["now"]["outcome"] == "saved"

    def test_notes_present_at_start_are_removed_and_recorded_stale(
            self, cam, worker):
        make_note("a", age=0.0)
        make_note("b", age=0.0)
        make_note("x", filename="warm")
        run(worker, startup=True)
        assert os.listdir(queue_dir()) == []
        rows = outcomes()
        assert sorted(r["name"] for r in rows) == ["a", "b"]
        assert {r["outcome"] for r in rows} == {"stale"}
        assert cam.events == []

    def test_every_note_is_removed_in_one_pass(self, cam, worker):
        for i in range(5):
            make_note(f"n{i}", age=i * 0.1)
        make_note("old", age=30)
        make_note("x", filename="warm")
        make_note("x", filename="garbage")
        run(worker)
        assert os.listdir(queue_dir()) == []

    def test_the_note_is_gone_before_the_capture_starts(self, cam, worker):
        make_note("a")
        seen = []
        cam.on_capture = lambda: seen.append(os.listdir(queue_dir()))
        run(worker)
        assert seen == [[]]

    def test_the_note_is_gone_before_the_camera_is_asked(self, cam, worker):
        make_note("a")
        seen = []
        real = cam.acquire_camera

        def acquire():
            seen.append(os.listdir(queue_dir()))
            return real()

        cam.acquire_camera = acquire
        run(worker)
        assert seen == [[]]

    def test_a_note_that_vanishes_mid_pass_is_skipped(self, cam, worker,
                                                      monkeypatch):
        make_note("a")
        make_note("b", age=0.01)
        real_unlink = os.unlink

        def unlink(path, *a, **k):
            if path.endswith("__a"):
                real_unlink(path)  # another process got there first
            return real_unlink(path, *a, **k)

        monkeypatch.setattr(turn_frames.os, "unlink", unlink)
        run(worker)
        rows = by_name(outcomes())
        assert "a" not in rows
        assert rows["b"]["outcome"] == "saved"


class TestBadNotes:
    @pytest.mark.parametrize("filename", [
        "garbage", "..__x", "2026-13-45__x", "2026-02-30__x", "20261031__x",
        "2026-10-31__", "2026-10-31__a b", "2026-10-31__x\n",
        "２０２６-10-31__x", "2026-10-31", "__x",
        "2026-10-31__" + "a" * 65, "9999-99-99__ok",
    ])
    def test_creates_no_path_and_no_outcome(self, cam, worker, tmp_path,
                                             filename):
        make_note("x", filename=filename)
        before = set(all_files(str(tmp_path)))
        run(worker)
        after = set(all_files(str(tmp_path)))
        assert os.listdir(queue_dir()) == []
        assert cam.events == []
        assert not [p for p in after - before if p.endswith(".jpg")]
        assert not [p for p in after - before if "outcomes" in p]
        assert os.listdir(frames_root()) == [".queue"]

    def test_a_bad_note_does_not_stop_a_good_one(self, cam, worker):
        make_note("x", filename="garbage")
        make_note("ok")
        run(worker)
        assert outcomes()[0]["outcome"] == "saved"

    def test_the_parser_accepts_the_request_shape(self):
        assert turn_frames._parse_note("2026-10-31__s_t01") == (
            "2026-10-31", "s_t01")

    def test_a_name_may_hold_a_double_underscore(self):
        assert turn_frames._parse_note("2026-10-31__a__b") == (
            "2026-10-31", "a__b")


# ---------------------------------------------------------------------------
# Worker pass: camera
# ---------------------------------------------------------------------------

class TestCamera:
    def test_a_warm_note_opens_the_camera_and_saves_nothing(self, cam, worker):
        make_note("x", filename="warm")
        run(worker)
        assert cam.events == ["acquire"]
        assert worker.held is True
        assert outcomes() == []
        assert not [p for p in all_files(frames_root()) if p.endswith(".jpg")]

    def test_a_stale_warm_note_is_ignored(self, cam, worker):
        make_note("x", filename="warm", age=30)
        run(worker)
        assert cam.events == []
        assert worker.held is False
        assert os.listdir(queue_dir()) == []

    def test_a_warm_note_and_a_fresh_frame_in_one_pass(self, cam, worker):
        make_note("x", filename="warm")
        make_note("pic")
        run(worker)
        rows = outcomes()
        assert [(r["name"], r["outcome"]) for r in rows] == [("pic", "saved")]
        assert cam.events == ["acquire", "capture"]

    def test_a_warm_note_is_newer_but_never_outranks_a_frame(self, cam, worker):
        make_note("pic", age=1.0)
        make_note("x", filename="warm", age=0.0)
        run(worker)
        assert [(r["name"], r["outcome"]) for r in outcomes()] == [
            ("pic", "saved")]

    def test_a_held_camera_is_not_acquired_again(self, cam, worker):
        make_note("a")
        run(worker)
        make_note("b")
        run(worker)
        assert cam.events.count("acquire") == 1
        assert cam.events.count("capture") == 2
        assert cam.refs == 1

    def test_refused_camera_records_failed_and_the_worker_goes_on(
            self, cam, worker):
        cam.acquire_error = RuntimeError("Camera __init__ sequence did not complete.")
        make_note("a")
        run(worker)
        row = outcomes()[0]
        assert row["outcome"] == "failed"
        assert row["error"] == (
            "RuntimeError: Camera __init__ sequence did not complete.")
        assert "release" not in cam.events
        assert worker.held is False
        cam.acquire_error = None
        worker.retry_open_at = 0.0  # the retry window has passed
        make_note("b")
        run(worker)
        assert by_name(outcomes())["b"]["outcome"] == "saved"

    def test_a_failed_acquire_releases_nothing(self, cam, worker):
        cam.acquire_error = CameraBusy("busy")
        make_note("a")
        run(worker)
        run(worker)
        assert "release" not in cam.events
        assert cam.refs == 0

    def test_a_failed_warm_acquire_records_nothing(self, cam, worker):
        cam.acquire_error = CameraBusy("busy")
        make_note("x", filename="warm")
        run(worker)
        assert outcomes() == []
        assert worker.held is False

    # -- I2: a refused open leaks one fd, so it is retried slowly -------------

    def test_a_refused_open_is_not_retried_inside_the_window(
            self, cam, worker, monkeypatch):
        monkeypatch.setattr(turn_frames, "_OPEN_RETRY_S", 1000.0)
        cam.acquire_error = RuntimeError("Camera __init__ sequence did not complete.")
        make_note("a")
        run(worker)
        assert cam.events.count("acquire") == 1
        assert worker.retry_open_at > time.monotonic() + 900
        cam.acquire_error = None  # the camera is fine now, but we must not look
        make_note("b")
        run(worker)
        assert cam.events.count("acquire") == 1
        rows = by_name(outcomes())
        assert rows["b"]["outcome"] == "failed"
        assert "refused" in rows["b"]["error"]
        assert "next open in" in rows["b"]["error"]
        assert worker.held is False

    def test_the_open_is_retried_after_the_window(self, cam, worker,
                                                  monkeypatch):
        monkeypatch.setattr(turn_frames, "_OPEN_RETRY_S", 1000.0)
        cam.acquire_error = RuntimeError("refused")
        make_note("a")
        run(worker)
        cam.acquire_error = None
        worker.retry_open_at = time.monotonic() - 0.01  # window over
        make_note("b")
        run(worker)
        assert cam.events.count("acquire") == 2
        assert by_name(outcomes())["b"]["outcome"] == "saved"
        assert worker.retry_open_at == 0.0

    def test_a_busy_camera_sets_no_backoff(self, cam, worker, monkeypatch):
        monkeypatch.setattr(turn_frames, "_OPEN_RETRY_S", 1000.0)
        cam.acquire_error = CameraBusy("busy")
        make_note("a")
        run(worker)
        assert by_name(outcomes())["a"]["outcome"] == "failed"
        assert worker.retry_open_at == 0.0
        cam.acquire_error = None
        make_note("b")
        run(worker)
        assert cam.events.count("acquire") == 2
        assert by_name(outcomes())["b"]["outcome"] == "saved"

    def test_a_warm_note_inside_the_window_does_nothing(self, cam, worker,
                                                        monkeypatch):
        monkeypatch.setattr(turn_frames, "_OPEN_RETRY_S", 1000.0)
        cam.acquire_error = RuntimeError("refused")
        make_note("a")
        run(worker)
        before = list(outcomes())
        make_note("x", filename="warm")
        run(worker)
        assert cam.events.count("acquire") == 1
        assert outcomes() == before
        assert worker.held is False

    def test_a_failed_warm_open_also_starts_the_window(self, cam, worker,
                                                       monkeypatch):
        monkeypatch.setattr(turn_frames, "_OPEN_RETRY_S", 1000.0)
        cam.acquire_error = RuntimeError("refused")
        make_note("x", filename="warm")
        run(worker)
        make_note("x", filename="warm")
        run(worker)
        assert cam.events.count("acquire") == 1

    def test_the_window_is_a_bounded_default(self):
        assert 60.0 <= turn_frames._OPEN_RETRY_S <= 600.0

    def test_the_error_text_is_cut_to_200_chars(self, cam, worker):
        cam.acquire_error = RuntimeError("x" * 500)
        make_note("a")
        run(worker)
        error = outcomes()[0]["error"]
        assert len(error) == 200
        assert error.startswith("RuntimeError: xxx")

    def test_a_failed_capture_releases_at_once(self, cam, worker):
        cam.capture_error = TimeoutError("no frame in 3.0 s")
        make_note("a")
        run(worker)
        assert cam.events == ["acquire", "capture", "release"]
        row = outcomes()[0]
        assert row["outcome"] == "failed"
        assert row["error"] == "TimeoutError: no frame in 3.0 s"
        assert row["taken_at"] is None
        assert worker.held is False
        assert cam.refs == 0

    def test_a_capture_failure_on_a_held_camera_releases_despite_the_hold(
            self, cam, worker):
        make_note("a")
        run(worker)
        assert worker.held is True
        assert worker.hold_until - time.monotonic() > 14.0
        cam.capture_error = TimeoutError("no frame")
        make_note("b")
        run(worker)
        assert cam.events[-2:] == ["capture", "release"]
        assert worker.held is False

    def test_a_capture_timeout_then_a_new_note_reopens_the_camera(
            self, cam, worker):
        cam.capture_error = TimeoutError("no frame")
        make_note("a")
        run(worker)
        cam.capture_error = None
        make_note("b")
        run(worker)
        assert cam.events == ["acquire", "capture", "release",
                              "acquire", "capture"]
        rows = by_name(outcomes())
        assert rows["a"]["outcome"] == "failed"
        assert rows["b"]["outcome"] == "saved"
        assert os.path.exists(os.path.join(frames_root(), today(), "b.jpg"))

    def test_a_capture_failure_is_one_capture_per_pass(self, cam, worker):
        cam.capture_error = TimeoutError("no frame")
        for i in range(3):
            make_note(f"n{i}", age=0.3 - i * 0.1)
        run(worker)
        assert cam.events.count("capture") == 1
        run(worker)
        run(worker)
        assert cam.events.count("capture") == 1

    def test_an_encode_failure_records_failed_and_keeps_the_hold(
            self, cam, worker, monkeypatch):
        monkeypatch.setattr(turn_frames, "_encode_and_save",
                            MagicMock(side_effect=OSError("disk full")))
        make_note("a")
        run(worker)
        row = outcomes()[0]
        assert row["outcome"] == "failed"
        assert row["error"] == "OSError: disk full"
        assert row["taken_at"] is not None
        assert "release" not in cam.events
        assert worker.held is True

    def test_a_to_pil_failure_keeps_the_hold(self, cam, worker):
        cam.to_pil = MagicMock(side_effect=ValueError("bad frame"))
        make_note("a")
        run(worker)
        assert outcomes()[0]["error"] == "ValueError: bad frame"
        assert worker.held is True

    def test_a_busy_release_keeps_the_ref_and_retries_on_a_later_pass(
            self, cam, worker):
        cam.capture_error = TimeoutError("no frame")
        cam.release_error = CameraBusy("lock held")
        make_note("a")
        run(worker)
        assert worker.held is True
        assert cam.refs == 1
        cam.events.clear()
        cam.release_error = None
        run(worker)
        assert cam.events == ["release"]
        assert worker.held is False
        assert cam.refs == 0

    def test_a_busy_release_retries_each_pass_until_it_works(self, cam, worker):
        cam.capture_error = TimeoutError("no frame")
        cam.release_error = CameraBusy("lock held")
        make_note("a")
        run(worker)
        run(worker)
        run(worker)
        assert cam.events.count("release") == 3
        assert worker.held is True

    def test_an_unexpected_release_error_drops_the_belief(self, cam, worker):
        cam.capture_error = TimeoutError("no frame")
        cam.release_error = ValueError("odd")
        make_note("a")
        run(worker)
        assert worker.held is False


class TestSettle:
    def test_waits_for_settle_on_a_fresh_open(self, cam, worker):
        make_note("a")
        stop = StepStop()
        run(worker, stop)
        waited = [w for w in stop.waits if w]
        assert len(waited) == 1
        assert 0.3 < waited[0] <= 0.5

    def test_uses_the_saved_settle_value(self, cam, worker, tmp_path):
        write_config(tmp_path, {"turn_frames_settle_s": 2.0})
        make_note("a")
        stop = StepStop()
        run(worker, stop)
        assert 1.8 < [w for w in stop.waits if w][0] <= 2.0

    def test_no_wait_on_a_camera_already_open_past_settle(self, cam, worker):
        cam.refs = 1
        cam.open_t = time.monotonic() - 60
        make_note("a")
        stop = StepStop()
        run(worker, stop)
        assert [w for w in stop.waits if w] == []
        assert cam.events == ["acquire", "capture"]

    def test_no_wait_when_opened_at_is_none(self, cam, worker):
        cam.opened_at_value = None
        make_note("a")
        stop = StepStop()
        run(worker, stop)
        assert [w for w in stop.waits if w] == []
        assert outcomes()[0]["outcome"] == "saved"

    def test_a_warm_note_does_not_wait(self, cam, worker):
        make_note("x", filename="warm")
        stop = StepStop()
        run(worker, stop)
        assert [w for w in stop.waits if w] == []

    def test_the_wait_uses_the_stop_event(self, cam, worker):
        make_note("a")
        stop = StepStop()
        run(worker, stop)
        assert stop.waits, "settle must wait on the stop event"

    def test_stop_during_settle_records_failed_and_takes_no_frame(
            self, cam, worker):
        make_note("a")
        stop = StepStop()
        stop.wait = lambda t=None: True
        run(worker, stop)
        assert "capture" not in cam.events
        row = outcomes()[0]
        assert row["outcome"] == "failed"
        assert "stop" in row["error"].lower()


class TestHold:
    def test_the_hold_is_15s_after_the_last_note(self, cam, worker):
        assert turn_frames._HOLD_S == 15.0
        make_note("a")
        run(worker)
        left = worker.hold_until - time.monotonic()
        assert 14.0 < left <= 15.0

    def test_a_warm_note_extends_the_hold(self, cam, worker):
        make_note("a")
        run(worker)
        worker.hold_until = time.monotonic() + 1.0
        make_note("x", filename="warm")
        run(worker)
        assert worker.hold_until - time.monotonic() > 14.0

    def test_no_release_before_the_hold_ends(self, cam, worker):
        make_note("a")
        run(worker)
        run(worker)
        run(worker)
        assert "release" not in cam.events
        assert worker.held is True

    def test_release_once_the_hold_has_ended(self, cam, worker):
        make_note("a")
        run(worker)
        worker.hold_until = time.monotonic() - 0.1
        run(worker)
        assert cam.events[-1] == "release"
        assert worker.held is False
        assert cam.refs == 0

    def test_a_new_note_after_the_release_reopens_the_camera(self, cam, worker):
        make_note("a")
        run(worker)
        worker.hold_until = time.monotonic() - 0.1
        run(worker)
        make_note("b")
        run(worker)
        assert cam.events.count("acquire") == 2
        assert by_name(outcomes())["b"]["outcome"] == "saved"

    def test_an_idle_worker_holding_nothing_never_touches_the_camera(
            self, cam, worker):
        run(worker)
        run(worker)
        assert cam.events == []

    def test_a_stale_note_extends_nothing_when_not_held(self, cam, worker):
        make_note("late", age=30)
        run(worker)
        assert worker.held is False


class TestHoldWhenAPassFails:
    """I1: a fault early in the pass must not keep the camera open."""

    def _held(self, cam, worker):
        make_note("a")
        run(worker)
        assert worker.held is True and cam.refs == 1

    @pytest.mark.parametrize("target", ["_ensure_queue", "_take_notes"])
    def test_an_expired_hold_releases_though_the_pass_raises(
            self, cam, worker, monkeypatch, target):
        self._held(cam, worker)
        worker.hold_until = time.monotonic() - 0.1
        self._break(monkeypatch, target)
        for _ in range(3):
            with pytest.raises(RuntimeError, match="boom"):
                run(worker)
        assert worker.held is False
        assert cam.refs == 0
        assert cam.events.count("release") == 1

    @pytest.mark.parametrize("target", ["_ensure_queue", "_take_notes"])
    def test_a_hold_not_yet_expired_stays_held(
            self, cam, worker, monkeypatch, target):
        self._held(cam, worker)
        self._break(monkeypatch, target)
        with pytest.raises(RuntimeError, match="boom"):
            run(worker)
        assert worker.held is True
        assert "release" not in cam.events

    @pytest.mark.parametrize("target", ["_ensure_queue", "_take_notes"])
    def test_last_pass_moves_on_a_pass_that_raises(
            self, cam, worker, monkeypatch, target):
        worker.last_pass = time.monotonic() - 100.0
        before = worker.last_pass
        self._break(monkeypatch, target)
        with pytest.raises(RuntimeError, match="boom"):
            run(worker)
        assert worker.last_pass > before + 99.0

    def test_a_busy_release_in_the_finally_keeps_the_original_error(
            self, cam, worker, monkeypatch):
        self._held(cam, worker)
        worker.hold_until = time.monotonic() - 0.1
        cam.release_error = CameraBusy("lock held")
        self._break(monkeypatch, "_take_notes")
        with pytest.raises(RuntimeError, match="boom"):
            run(worker)
        assert worker.held is True  # kept for a later pass

    def test_the_loop_still_logs_the_original_exception(
            self, cam, worker, monkeypatch):
        fake_log = MagicMock()
        monkeypatch.setattr(turn_frames, "log", fake_log)
        self._held(cam, worker)
        worker.hold_until = time.monotonic() - 0.1
        worker.startup_done = True
        self._break(monkeypatch, "_take_notes")
        worker._main(StepStop(passes=2))
        messages = [str(c) for c in fake_log.warning.call_args_list]
        assert any("boom" in m for m in messages)
        assert cam.refs == 0

    @staticmethod
    def _break(monkeypatch, target):
        def boom(*a, **k):
            raise RuntimeError("boom")

        if target == "_take_notes":
            monkeypatch.setattr(turn_frames._Worker, "_take_notes", boom)
        else:
            monkeypatch.setattr(turn_frames, target, boom)


class TestStopReleases:
    def test_the_loop_releases_a_held_camera_on_exit(self, cam, worker):
        worker.startup_done = True
        make_note("a")
        worker._main(StepStop(passes=2))
        assert cam.events[-1] == "release"
        assert cam.refs == 0

    def test_a_busy_release_at_exit_is_tried_once_and_never_raises(
            self, cam, worker):
        worker.startup_done = True
        make_note("a")
        cam.release_error = CameraBusy("lock held")
        worker._main(StepStop(passes=2))
        assert cam.events.count("release") == 1
        assert worker.held is True

    def test_nothing_is_released_when_nothing_is_held(self, cam, worker):
        worker._main(StepStop(passes=2))
        assert cam.events == []


# ---------------------------------------------------------------------------
# Saved frame
# ---------------------------------------------------------------------------

class TestSave:
    def test_size_colour_and_no_tmp(self, cam, worker):
        from PIL import Image
        make_note("a")
        run(worker)
        path = os.path.join(frames_root(), today(), "a.jpg")
        with Image.open(path) as img:
            assert img.format == "JPEG"
            assert img.size == (1280, 720)
            r, g, b = img.convert("RGB").getpixel((640, 360))
        assert abs(r - 20) < 14 and abs(g - 30) < 14 and abs(b - 200) < 14
        assert not [n for n in os.listdir(os.path.dirname(path))
                    if n.endswith(".tmp")]

    def test_a_full_size_1080p_frame_is_resized(self, cam, worker):
        from PIL import Image
        cam.frame_shape = (1080, 1920)
        make_note("a")
        run(worker)
        with Image.open(os.path.join(frames_root(), today(), "a.jpg")) as img:
            assert img.size == (1280, 720)

    def test_blue_in_memory_is_not_swapped_to_red(self, cam, worker):
        from PIL import Image
        cam.frame_bgr = (255, 0, 0)
        make_note("a")
        run(worker)
        with Image.open(os.path.join(frames_root(), today(), "a.jpg")) as img:
            r, g, b = img.convert("RGB").getpixel((10, 10))
        assert b > 200 and r < 40

    def test_modes_at_every_level(self, cam, worker):
        make_note("a")
        run(worker)
        day = os.path.join(frames_root(), today())
        assert mode_of(frames_root()) == 0o700
        assert mode_of(queue_dir()) == 0o700
        assert mode_of(day) == 0o700
        assert mode_of(os.path.join(day, "a.jpg")) == 0o600
        assert mode_of(os.path.join(day, "outcomes.jsonl")) == 0o600

    def test_modes_hold_under_a_permissive_umask(self, cam, worker):
        os.umask(0o000)
        try:
            make_note("a")
            run(worker)
        finally:
            os.umask(0o022)
        day = os.path.join(frames_root(), today())
        assert mode_of(frames_root()) == 0o700
        assert mode_of(day) == 0o700
        assert mode_of(os.path.join(day, "a.jpg")) == 0o600
        assert mode_of(os.path.join(day, "outcomes.jsonl")) == 0o600

    def test_a_failed_write_leaves_no_jpg_and_no_tmp(self, cam, worker):
        class Broken:
            def resize(self, *a, **k):
                return self

            def save(self, fp, **k):
                fp.write(b"half a jpeg")
                raise OSError("disk full")

        cam.to_pil = lambda frame: Broken()
        make_note("a")
        run(worker)
        day = os.path.join(frames_root(), today())
        assert os.listdir(day) == ["outcomes.jsonl"]
        assert outcomes()[0]["outcome"] == "failed"

    def test_no_jpg_exists_while_the_bytes_are_being_written(self, cam,
                                                             worker):
        """A reader must never see half a file: write a .tmp, then rename."""
        seen = {}

        class Spy:
            def resize(self, *a, **k):
                return self

            def save(self, fp, **k):
                day = os.path.join(frames_root(), today())
                fp.write(b"first half")
                fp.flush()
                seen["during"] = sorted(os.listdir(day))
                fp.write(b"second half")

        cam.to_pil = lambda frame: Spy()
        make_note("a")
        run(worker)
        assert "a.jpg" not in seen["during"]
        assert "a.jpg.tmp" in seen["during"]
        day = os.path.join(frames_root(), today())
        assert sorted(os.listdir(day)) == ["a.jpg", "outcomes.jsonl"]
        with open(os.path.join(day, "a.jpg"), "rb") as f:
            assert f.read() == b"first halfsecond half"

    def test_a_failed_rename_leaves_no_tmp(self, cam, worker, monkeypatch):
        monkeypatch.setattr(turn_frames.os, "replace",
                            MagicMock(side_effect=OSError("nope")))
        make_note("a")
        run(worker)
        day = os.path.join(frames_root(), today())
        assert os.listdir(day) == ["outcomes.jsonl"]

    def test_the_folder_is_named_for_the_note_date_not_today(self, cam, worker):
        make_note("a", day="2031-05-06")
        run(worker)
        assert os.path.exists(os.path.join(frames_root(), "2031-05-06", "a.jpg"))
        assert outcomes("2031-05-06")[0]["outcome"] == "saved"

    def test_two_processes_racing_to_make_a_folder_is_fine(self, cam, worker,
                                                           monkeypatch):
        real = os.mkdir
        calls = []

        def racing_mkdir(path, mode=0o777, **k):
            calls.append(path)
            real(path, mode)
            raise FileExistsError(path)

        monkeypatch.setattr(turn_frames.os, "mkdir", racing_mkdir)
        make_note("a")
        run(worker)
        assert outcomes()[0]["outcome"] == "saved"
        assert calls

    def test_the_above_folder_is_made_but_its_mode_is_left(self, on, tmp_path,
                                                          monkeypatch):
        parent = tmp_path / "fresh"
        monkeypatch.setenv("BENDER_LOG_DIR", str(parent))
        turn_frames.request("a")
        assert (parent / "frames" / ".queue").is_dir()
        assert mode_of(str(parent)) == 0o755


# ---------------------------------------------------------------------------
# outcomes.jsonl
# ---------------------------------------------------------------------------

class TestOutcomes:
    def test_exact_keys(self, cam, worker):
        make_note("a")
        run(worker)
        assert list(outcomes()[0]) == [
            "name", "outcome", "error", "requested_at", "taken_at"]

    def test_saved_row_values(self, cam, worker):
        make_note("a", age=0.5)
        run(worker)
        row = outcomes()[0]
        assert row["name"] == "a"
        assert row["error"] is None
        req = datetime.datetime.fromisoformat(row["requested_at"])
        taken = datetime.datetime.fromisoformat(row["taken_at"])
        assert req.tzinfo is not None and taken.tzinfo is not None
        assert taken >= req
        assert abs((datetime.datetime.now().astimezone() - taken)
                   .total_seconds()) < 30

    @pytest.mark.parametrize("age,outcome", [(9.0, "stale")])
    def test_stale_row_has_null_taken_at_and_error(self, cam, worker, age,
                                                   outcome):
        make_note("a", age=age)
        run(worker)
        row = outcomes()[0]
        assert row["outcome"] == outcome
        assert row["taken_at"] is None
        assert row["error"] is None
        assert datetime.datetime.fromisoformat(
            row["requested_at"]).tzinfo is not None

    def test_dropped_row_has_null_taken_at(self, cam, worker):
        make_note("a", age=1.0)
        make_note("b")
        run(worker)
        assert by_name(outcomes())["a"]["taken_at"] is None

    def test_requested_at_is_the_note_mtime(self, cam, worker):
        path = make_note("a", age=2.0)
        mtime = os.stat(path).st_mtime
        run(worker)
        req = datetime.datetime.fromisoformat(outcomes()[0]["requested_at"])
        assert abs(req.timestamp() - mtime) < 0.01

    def test_appends_one_line_per_note(self, cam, worker):
        for i in range(3):
            make_note(f"n{i}")
            run(worker)
        assert [r["name"] for r in outcomes()] == ["n0", "n1", "n2"]

    def test_one_os_write_on_an_append_fd(self, cam, worker, monkeypatch):
        flags = []
        writes = []
        real_open, real_write = os.open, os.write

        def spy_open(path, fl, *a, **k):
            fd = real_open(path, fl, *a, **k)
            if str(path).endswith("outcomes.jsonl"):
                flags.append(fl)
            return fd

        def spy_write(fd, data):
            writes.append(data)
            return real_write(fd, data)

        monkeypatch.setattr(turn_frames.os, "open", spy_open)
        monkeypatch.setattr(turn_frames.os, "write", spy_write)
        make_note("a")
        run(worker)
        assert flags and flags[0] & os.O_APPEND
        assert len(writes) == 1 and writes[0].endswith(b"\n")

    def test_a_failure_to_write_an_outcome_never_stops_the_worker(
            self, cam, worker, monkeypatch):
        monkeypatch.setattr(turn_frames.os, "write",
                            MagicMock(side_effect=OSError("full")))
        make_note("a")
        make_note("b", age=-0.001)
        run(worker)
        assert os.path.exists(os.path.join(frames_root(), today(), "b.jpg"))


# ---------------------------------------------------------------------------
# Survival
# ---------------------------------------------------------------------------

class TestSurvival:
    def test_worker_recreates_queue_after_the_folder_is_deleted(
            self, cam, worker):
        import shutil
        run(worker)
        shutil.rmtree(frames_root())
        run(worker)
        assert os.path.isdir(queue_dir())
        make_note("a")
        run(worker)
        assert outcomes()[0]["outcome"] == "saved"

    def test_the_thread_recreates_queue_after_rm_r(self, cam, monkeypatch):
        import shutil
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        turn_frames.start_worker()
        assert wait_for(lambda: os.path.isdir(queue_dir()))
        # The worker stays live, as it does when the owner runs `rm -r`. The
        # delete races it and can fail with "directory not empty", so retry.
        # The worker skips folders in .queue, so this one goes only with the
        # delete, and its absence proves the old .queue was really removed.
        marker = os.path.join(queue_dir(), "marker")
        os.mkdir(marker)

        def deleted():
            shutil.rmtree(frames_root(), ignore_errors=True)
            return not os.path.exists(marker)

        assert wait_for(deleted)
        assert wait_for(lambda: os.path.isdir(queue_dir()))

    @pytest.mark.parametrize("target", ["_sweep", "_take_notes",
                                        "_ensure_queue"])
    def test_a_raise_anywhere_in_the_loop_does_not_end_the_thread(
            self, cam, monkeypatch, target):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        fail_n = 1 if target == "_sweep" else 3  # the sweep runs once an hour
        calls = {"n": 0}
        if target == "_take_notes":
            real = turn_frames._Worker._take_notes

            def flaky(self, q):
                calls["n"] += 1
                if calls["n"] <= fail_n:
                    raise RuntimeError("boom")
                return real(self, q)

            monkeypatch.setattr(turn_frames._Worker, "_take_notes", flaky)
        else:
            real = getattr(turn_frames, target)

            def flaky(*a, **k):
                calls["n"] += 1
                if calls["n"] <= fail_n:
                    raise RuntimeError("boom")
                return real(*a, **k)

            monkeypatch.setattr(turn_frames, target, flaky)
        turn_frames.start_worker()
        assert wait_for(lambda: calls["n"] >= fail_n)
        assert wait_for(lambda: turn_frames._current.startup_done)
        assert turn_frames._current.thread.is_alive()
        make_note("after")
        assert wait_for(lambda: outcomes() != [])
        assert outcomes()[0]["outcome"] == "saved"

    def test_a_repeated_failure_logs_at_most_once_a_minute(self, cam, worker,
                                                           monkeypatch):
        fake_log = MagicMock()
        monkeypatch.setattr(turn_frames, "log", fake_log)
        monkeypatch.setattr(turn_frames._Worker, "run_pass",
                            MagicMock(side_effect=RuntimeError("boom")))
        worker._main(StepStop(passes=200))
        assert fake_log.warning.call_count + fake_log.error.call_count \
            + fake_log.exception.call_count == 1

    def test_failure_logs_again_after_a_minute(self, cam, worker, monkeypatch):
        fake_log = MagicMock()
        monkeypatch.setattr(turn_frames, "log", fake_log)
        monkeypatch.setattr(turn_frames, "_LOG_EVERY_S", 0.0)
        monkeypatch.setattr(turn_frames._Worker, "run_pass",
                            MagicMock(side_effect=RuntimeError("boom")))
        worker._main(StepStop(passes=5))
        assert fake_log.warning.call_count >= 2

    def test_the_loop_waits_between_failed_passes(self, cam, worker,
                                                  monkeypatch):
        monkeypatch.setattr(turn_frames._Worker, "run_pass",
                            MagicMock(side_effect=RuntimeError("boom")))
        stop = StepStop(passes=4)
        worker._main(stop)
        assert stop.waits.count(turn_frames._POLL_S) == 4

    def test_a_pass_that_raised_before_the_flush_repeats_the_startup_rules(
            self, cam, worker, monkeypatch):
        make_note("a")
        real = turn_frames._sweep
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("boom")
            return real()

        monkeypatch.setattr(turn_frames, "_sweep", flaky)
        worker._main(StepStop(passes=3))
        assert outcomes()[0]["outcome"] == "stale"
        assert cam.events == [] or "capture" not in cam.events

    def test_the_first_pass_of_the_thread_flushes_old_notes(self, cam,
                                                            monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        make_note("old1")
        make_note("old2")
        turn_frames.start_worker()
        assert wait_for(lambda: len(outcomes()) == 2)
        assert {r["outcome"] for r in outcomes()} == {"stale"}
        assert "capture" not in cam.events


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def make_day(name, files=("x.jpg",)):
    d = os.path.join(frames_root(), name)
    os.makedirs(d, exist_ok=True)
    for f in files:
        with open(os.path.join(d, f), "w") as fh:
            fh.write("x")
    return d


def days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


class TestSweep:
    def test_removes_expired_dated_folders_only(self):
        old = make_day(days_ago(10))
        edge = make_day(days_ago(7))
        young = make_day(days_ago(6))
        now = make_day(days_ago(0))
        os.makedirs(queue_dir(), exist_ok=True)
        turn_frames._sweep()
        assert not os.path.exists(old)
        assert not os.path.exists(edge)
        assert os.path.exists(young)
        assert os.path.exists(now)
        assert os.path.isdir(queue_dir())

    def test_ignores_every_other_name(self):
        keep = []
        for name in ["notes", ".queue", "2026-13-45", "2020-01-01.bak",
                     "x2020-01-01", "2020-01-01 ", "20200101", "2020-1-1",
                     "２０２０-01-01"]:
            keep.append(make_day(name))
        turn_frames._sweep()
        assert all(os.path.exists(p) for p in keep)

    def test_a_plain_file_with_a_date_name_is_left(self):
        os.makedirs(frames_root(), exist_ok=True)
        path = os.path.join(frames_root(), "2020-01-01")
        with open(path, "w") as f:
            f.write("x")
        turn_frames._sweep()
        assert os.path.exists(path)

    def test_a_symlink_with_a_date_name_is_never_followed(self, tmp_path):
        target = tmp_path / "precious"
        target.mkdir()
        (target / "keep.txt").write_text("x")
        os.makedirs(frames_root(), exist_ok=True)
        link = os.path.join(frames_root(), "2020-01-01")
        os.symlink(str(target), link)
        turn_frames._sweep()
        assert (target / "keep.txt").exists()
        assert os.path.islink(link)

    def test_removes_a_leftover_tmp_with_its_folder(self):
        d = make_day(days_ago(30), files=("a.jpg.tmp", "outcomes.jsonl"))
        turn_frames._sweep()
        assert not os.path.exists(d)

    def test_a_future_date_is_kept(self):
        d = make_day(
            (datetime.date.today() + datetime.timedelta(days=400)).isoformat())
        turn_frames._sweep()
        assert os.path.exists(d)

    @pytest.mark.parametrize("value", [None, 0, -3, 0.5, True, "7", "", [], {}])
    def test_a_bad_retention_means_7_and_deletes(self, tmp_path, value):
        write_config(tmp_path, {"turn_frames_retention_days": value})
        old = make_day(days_ago(8))
        young = make_day(days_ago(6))
        turn_frames._sweep()
        assert not os.path.exists(old)
        assert os.path.exists(young)

    def test_a_bad_retention_in_cfg_means_7_too(self, monkeypatch):
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_retention_days", 0)
        old = make_day(days_ago(8))
        young = make_day(days_ago(6))
        turn_frames._sweep()
        assert not os.path.exists(old)
        assert os.path.exists(young)

    def test_a_good_retention_in_the_file_wins_over_stale_cfg(self, tmp_path,
                                                              monkeypatch):
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_retention_days", 30)
        write_config(tmp_path, {"turn_frames_retention_days": 2})
        gone = make_day(days_ago(3))
        turn_frames._sweep()
        assert not os.path.exists(gone)

    def test_cfg_is_used_when_the_file_lacks_the_key(self, monkeypatch):
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_retention_days", 2)
        gone = make_day(days_ago(3))
        turn_frames._sweep()
        assert not os.path.exists(gone)

    @pytest.mark.parametrize("content", ["{not json", "[]", "null", ""])
    def test_a_corrupt_config_file_uses_the_default(self, tmp_path, content):
        write_config(tmp_path, content)
        old = make_day(days_ago(8))
        young = make_day(days_ago(6))
        turn_frames._sweep()
        assert not os.path.exists(old)
        assert os.path.exists(young)

    def test_infinite_retention_is_refused(self, tmp_path):
        (tmp_path / "bender_config.json").write_text(
            '{"turn_frames_retention_days": Infinity}')
        old = make_day(days_ago(8))
        turn_frames._sweep()
        assert not os.path.exists(old)

    def test_never_raises_when_the_folder_is_missing(self):
        turn_frames._sweep()

    def test_never_raises_when_frames_is_a_file(self):
        os.makedirs(os.path.dirname(frames_root()), exist_ok=True)
        with open(frames_root(), "w") as f:
            f.write("x")
        turn_frames._sweep()

    def test_never_raises_when_listing_fails(self, monkeypatch):
        make_day(days_ago(30))
        monkeypatch.setattr(turn_frames.os, "scandir",
                            MagicMock(side_effect=OSError("denied")))
        turn_frames._sweep()

    def test_one_bad_folder_does_not_stop_the_others(self, monkeypatch):
        a = make_day(days_ago(20))
        b = make_day(days_ago(21))
        c = make_day(days_ago(22))
        real = turn_frames.shutil.rmtree
        state = {"n": 0}

        def flaky(path, *args, **kw):
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("busy")
            return real(path, *args, **kw)

        monkeypatch.setattr(turn_frames.shutil, "rmtree", flaky)
        turn_frames._sweep()
        gone = [not os.path.exists(p) for p in (a, b, c)]
        assert sorted(gone) == [False, True, True]

    def test_runs_with_the_feature_off(self, cam, worker):
        assert turn_frames.enabled() is False
        old = make_day(days_ago(30))
        run(worker)
        assert not os.path.exists(old)

    def test_runs_at_start_then_hourly(self, cam, worker, monkeypatch):
        sweep = MagicMock()
        monkeypatch.setattr(turn_frames, "_sweep", sweep)
        run(worker)
        run(worker)
        assert sweep.call_count == 1
        worker.last_sweep = time.monotonic() - turn_frames._SWEEP_EVERY_S - 1
        run(worker)
        assert sweep.call_count == 2

    def test_the_sweep_interval_is_an_hour(self):
        assert turn_frames._SWEEP_EVERY_S == 3600.0


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSettings:
    @pytest.mark.parametrize("value", [None, True, False, "0.5", [], -0.1,
                                       5.01, 100, float("nan")])
    def test_a_bad_settle_gives_the_default(self, tmp_path, value):
        (tmp_path / "bender_config.json").write_text(
            json.dumps({"turn_frames_settle_s": value}))
        assert turn_frames._settle_s() == 0.5

    @pytest.mark.parametrize("value,expected", [(0, 0.0), (0.0, 0.0),
                                                (1, 1.0), (2.5, 2.5),
                                                (5, 5.0)])
    def test_a_good_settle_is_used(self, tmp_path, value, expected):
        write_config(tmp_path, {"turn_frames_settle_s": value})
        assert turn_frames._settle_s() == expected

    def test_settle_falls_back_to_cfg_then_default(self, monkeypatch):
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_settle_s", 1.5)
        assert turn_frames._settle_s() == 1.5
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_settle_s", None)
        assert turn_frames._settle_s() == 0.5

    def test_retention_defaults(self, monkeypatch):
        assert turn_frames._retention_days() == 7
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_retention_days", None)
        assert turn_frames._retention_days() == 7

    def test_the_file_is_read_at_use_so_a_save_is_seen(self, tmp_path):
        write_config(tmp_path, {"turn_frames_retention_days": 3})
        assert turn_frames._retention_days() == 3
        write_config(tmp_path, {"turn_frames_retention_days": 9})
        assert turn_frames._retention_days() == 9

    def test_enabled_saved_reads_the_file(self, tmp_path):
        write_config(tmp_path, {"turn_frames_enabled": True})
        assert turn_frames.status()["enabled_saved"] is True
        write_config(tmp_path, {"turn_frames_enabled": False})
        assert turn_frames.status()["enabled_saved"] is False

    @pytest.mark.parametrize("value", [None, "true", 1, "yes"])
    def test_enabled_saved_is_strict(self, tmp_path, value):
        write_config(tmp_path, {"turn_frames_enabled": value})
        assert turn_frames.status()["enabled_saved"] is False

    def test_enabled_saved_falls_back_to_cfg(self, monkeypatch):
        monkeypatch.setattr(turn_frames.cfg, "turn_frames_enabled", True)
        assert turn_frames.status()["enabled_saved"] is True


class TestOneDirectory:
    def test_frames_dir_honours_the_env_var_at_call_time(self, monkeypatch,
                                                         tmp_path):
        monkeypatch.setenv("BENDER_LOG_DIR", str(tmp_path / "a"))
        assert turn_frames.frames_dir() == str(tmp_path / "a" / "frames")
        monkeypatch.setenv("BENDER_LOG_DIR", str(tmp_path / "b"))
        assert turn_frames.frames_dir() == str(tmp_path / "b" / "frames")

    def test_without_the_env_var_it_is_repo_logs_frames(self, monkeypatch):
        monkeypatch.delenv("BENDER_LOG_DIR")
        assert turn_frames.frames_dir() == os.path.join(REPO, "logs", "frames")
        monkeypatch.setenv("BENDER_LOG_DIR", "")
        assert turn_frames.frames_dir() == os.path.join(REPO, "logs", "frames")

    def test_the_suite_writes_nothing_under_the_repo_logs(self):
        assert not turn_frames.frames_dir().startswith(
            os.path.join(REPO, "logs"))

    def test_writer_sweep_and_status_agree(self, on, cam, worker, tmp_path,
                                           monkeypatch):
        other = tmp_path / "elsewhere"
        monkeypatch.setenv("BENDER_LOG_DIR", str(other))
        assert turn_frames.request("a") is not None
        run(worker, startup=True)  # sweep + flush
        assert turn_frames.status()["stale_today"] == 1
        assert (other / "frames" / today() / "outcomes.jsonl").exists()
        old = other / "frames" / days_ago(40)
        old.mkdir()
        turn_frames._sweep()
        assert not old.exists()


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

class TestStatus:
    def test_keys(self):
        assert set(turn_frames.status()) == {
            "enabled_saved", "saved_today", "failed_today", "stale_today",
            "dropped_today", "last_saved", "waiting_notes",
            "oldest_note_age_s", "worker_alive", "worker_stuck_s"}

    def test_empty_state(self):
        s = turn_frames.status()
        assert s["saved_today"] == s["failed_today"] == 0
        assert s["stale_today"] == s["dropped_today"] == 0
        assert s["last_saved"] is None
        assert s["waiting_notes"] == 0
        assert s["oldest_note_age_s"] is None
        assert s["worker_alive"] is False
        assert s["worker_stuck_s"] is None

    def test_counts_come_from_outcomes(self):
        d = make_day(today(), files=())
        rows = [
            {"name": "a", "outcome": "saved", "error": None,
             "requested_at": "r", "taken_at": "2026-10-31T10:00:00+00:00"},
            {"name": "b", "outcome": "saved", "error": None,
             "requested_at": "r", "taken_at": "2026-10-31T11:00:00+00:00"},
            {"name": "c", "outcome": "failed", "error": "x",
             "requested_at": "r", "taken_at": None},
            {"name": "d", "outcome": "stale", "error": None,
             "requested_at": "r", "taken_at": None},
            {"name": "e", "outcome": "dropped", "error": None,
             "requested_at": "r", "taken_at": None},
            {"name": "f", "outcome": "dropped", "error": None,
             "requested_at": "r", "taken_at": None},
        ]
        with open(os.path.join(d, "outcomes.jsonl"), "w") as f:
            f.write("this line is not json\n")
            for r in rows:
                f.write(json.dumps(r) + "\n")
            f.write("[1, 2]\n")
            f.write('{"outcome": "weird"}\n')
            f.write("\n")
        s = turn_frames.status()
        assert (s["saved_today"], s["failed_today"], s["stale_today"],
                s["dropped_today"]) == (2, 1, 1, 2)
        assert s["last_saved"] == "2026-10-31T11:00:00+00:00"

    def test_a_tmp_or_an_orphan_jpg_changes_no_count(self):
        d = make_day(today(), files=("a.jpg.tmp", "orphan.jpg"))
        s = turn_frames.status()
        assert s["saved_today"] == 0
        assert s["last_saved"] is None
        assert os.path.exists(os.path.join(d, "orphan.jpg"))

    def test_other_days_are_not_counted(self):
        d = make_day(days_ago(1), files=())
        with open(os.path.join(d, "outcomes.jsonl"), "w") as f:
            f.write(json.dumps({"name": "a", "outcome": "saved",
                                "taken_at": "t"}) + "\n")
        assert turn_frames.status()["saved_today"] == 0

    def test_waiting_notes_and_oldest_age(self):
        make_note("a", age=2.0)
        make_note("b", age=0.0)
        make_note("x", filename="warm", age=1.0)
        s = turn_frames.status()
        assert s["waiting_notes"] == 3
        assert 1.9 <= s["oldest_note_age_s"] <= 3.0
        assert s["oldest_note_age_s"] == round(s["oldest_note_age_s"], 1)

    def test_oldest_age_uses_abs_for_a_future_note(self):
        make_note("a", age=-100.0)
        assert 99.0 <= turn_frames.status()["oldest_note_age_s"] <= 101.0

    def test_status_never_touches_the_camera(self, monkeypatch):
        class Explodes:
            def __getattr__(self, name):
                raise AssertionError(f"status touched camera.{name}")

        monkeypatch.setattr(turn_frames, "camera", Explodes())
        make_note("a")
        turn_frames.status()

    def test_never_raises_on_a_broken_disk(self, monkeypatch):
        os.makedirs(os.path.dirname(frames_root()), exist_ok=True)
        with open(frames_root(), "w") as f:
            f.write("a file where the folder should be")
        s = turn_frames.status()
        assert s["waiting_notes"] == 0 and s["saved_today"] == 0

    def test_never_raises_when_everything_raises(self, monkeypatch):
        monkeypatch.setattr(turn_frames, "frames_dir",
                            MagicMock(side_effect=RuntimeError("x")))
        s = turn_frames.status()
        assert s["worker_alive"] is False

    def test_a_stuck_worker_shows_its_age(self):
        w = turn_frames._Worker()
        w.last_pass = time.monotonic() - 42.0
        w.thread = threading.Thread(target=lambda: w.stop.wait(5), daemon=True)
        w.thread.start()
        turn_frames._current = w
        s = turn_frames.status()
        assert s["worker_alive"] is True
        assert 41.5 <= s["worker_stuck_s"] <= 43.5
        assert s["worker_stuck_s"] == round(s["worker_stuck_s"], 1)

    def test_a_dead_worker_is_reported_dead_with_a_raw_number(self):
        w = turn_frames._Worker()
        w.last_pass = time.monotonic() - 10.0
        w.thread = threading.Thread(target=lambda: None)
        w.thread.start()
        w.thread.join()
        turn_frames._current = w
        s = turn_frames.status()
        assert s["worker_alive"] is False
        assert s["worker_stuck_s"] >= 9.9

    def test_the_heartbeat_moves_every_pass(self, cam, worker):
        run(worker)
        first = worker.last_pass
        time.sleep(0.01)
        run(worker)
        assert worker.last_pass > first


# ---------------------------------------------------------------------------
# start_worker() and stop_worker()
# ---------------------------------------------------------------------------

def start_ready():
    """Start the real thread and wait for its startup flush to finish."""
    turn_frames.start_worker()
    assert wait_for(lambda: turn_frames._current.startup_done)


def worker_threads():
    return [t for t in threading.enumerate()
            if t.name == "turn-frames-worker" and t.is_alive()]


class TestThread:
    def test_start_is_idempotent(self, cam, monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        turn_frames.start_worker()
        turn_frames.start_worker()
        turn_frames.start_worker()
        assert len(worker_threads()) == 1

    def test_the_thread_is_a_named_daemon(self, cam, monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        turn_frames.start_worker()
        t = turn_frames._current.thread
        assert t.daemon is True
        assert t.name == "turn-frames-worker"

    def test_stop_ends_the_thread_promptly(self, cam, monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        turn_frames.start_worker()
        assert wait_for(lambda: turn_frames.status()["worker_alive"])
        started = time.monotonic()
        turn_frames.stop_worker()
        assert time.monotonic() - started < 1.5
        assert worker_threads() == []
        assert turn_frames.status()["worker_alive"] is False

    def test_stop_without_a_worker_is_fine(self):
        turn_frames.stop_worker()

    def test_restart_after_stop_makes_a_new_worker(self, cam, monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        turn_frames.start_worker()
        first = turn_frames._current
        turn_frames.stop_worker()
        turn_frames.start_worker()
        assert turn_frames._current is not first
        assert len(worker_threads()) == 1

    def test_each_worker_owns_its_stop_event(self, cam, monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        turn_frames.start_worker()
        first = turn_frames._current
        turn_frames.stop_worker()
        turn_frames.start_worker()
        assert first.stop is not turn_frames._current.stop
        assert first.stop.is_set()
        assert not turn_frames._current.stop.is_set()

    def test_start_never_raises(self, monkeypatch):
        monkeypatch.setattr(turn_frames.threading, "Thread",
                            MagicMock(side_effect=RuntimeError("no threads")))
        turn_frames.start_worker()

    def test_stop_does_not_wait_on_a_stuck_camera(self, cam, monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        gate = threading.Event()
        entered = threading.Event()

        def stuck_acquire():
            entered.set()
            gate.wait(10)
            raise RuntimeError("released late")

        cam.acquire_camera = stuck_acquire
        start_ready()
        make_note("a")
        assert entered.wait(5)
        started = time.monotonic()
        turn_frames.stop_worker()
        assert time.monotonic() - started < 1.5
        stuck = turn_frames._current
        assert stuck.thread.is_alive()
        # A later start cannot revive it or add a second thread.
        turn_frames.start_worker()
        assert turn_frames._current is stuck
        assert len(worker_threads()) == 1
        gate.set()
        stuck.thread.join(5)
        assert not stuck.thread.is_alive()

    def test_end_to_end_with_the_real_thread(self, on, cam, monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        monkeypatch.setattr(turn_frames, "_HOLD_S", 0.05)
        cam.opened_at_value = None  # no settle wait
        start_ready()
        path = turn_frames.request("e2e_t01")
        assert path is not None
        assert wait_for(lambda: turn_frames.status()["saved_today"] == 1)
        assert os.path.exists(os.path.join(frames_root(), path))
        assert wait_for(lambda: cam.refs == 0)
        assert cam.events.count("acquire") == 1
        assert cam.events[-1] == "release"

    def test_warm_through_the_real_thread_opens_the_camera(self, on, cam,
                                                          monkeypatch):
        monkeypatch.setattr(turn_frames, "_POLL_S", 0.01)
        monkeypatch.setattr(turn_frames, "_HOLD_S", 0.2)
        start_ready()
        turn_frames.warm()
        assert wait_for(lambda: "acquire" in cam.events)
        assert wait_for(lambda: cam.refs == 0)
        assert outcomes() == []


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

class TestImport:
    def test_importing_does_not_pull_in_picamera2_or_pil(self, tmp_path):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import turn_frames\n"
            "bad = [m for m in ('picamera2', 'PIL') if m in sys.modules]\n"
            "assert not bad, bad\n"
            "print('ok')\n" % os.path.join(REPO, "scripts")
        )
        env = dict(os.environ, BENDER_LOG_DIR=str(tmp_path))
        out = subprocess.run([sys.executable, "-I", "-c", code], env=env,
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "ok"

    def test_importing_creates_nothing(self, tmp_path):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import turn_frames\n" % os.path.join(REPO, "scripts")
        )
        env = dict(os.environ, BENDER_LOG_DIR=str(tmp_path / "fresh"))
        subprocess.run([sys.executable, "-I", "-c", code], env=env,
                       capture_output=True, text=True, timeout=60, check=True)
        assert not (tmp_path / "fresh" / "frames").exists()

    def test_constants(self):
        assert turn_frames._POLL_S == 0.2
        assert turn_frames._STALE_S == 5.0
        assert turn_frames._MAX_WAITING == 32
        assert turn_frames._SIZE == (1280, 720)
        assert turn_frames._JPEG_QUALITY == 80
        assert turn_frames._DEFAULT_RETENTION_DAYS == 7
        assert turn_frames._DEFAULT_SETTLE_S == 0.5
