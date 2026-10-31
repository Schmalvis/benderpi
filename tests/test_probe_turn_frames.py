"""Tests for scripts/probe_turn_frames.py.

The probe is a device check. Nobody runs it against a camera here. Every test
uses a fake camera module, and every test sets BENDER_LOG_DIR to tmp_path so no
note or frame lands under the repo's logs/. An autouse fixture blocks the real
subprocess, so no test can reach systemctl.
"""
import os
import subprocess
import sys

import numpy as np
import pytest

import camera
import probe_turn_frames as probe

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_SESSION_FILE = probe._session_file   # taken before the autouse fixture swaps it
REAL_RUN = subprocess.run   # for the tests' own child Python, taken before the guard
SCRIPT = os.path.join(REPO, "scripts", "probe_turn_frames.py")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeCam:
    """The names the probe uses from the camera module."""

    def __init__(self):
        self.events = []
        self.refs = 0
        self._CAPTURE_TIMEOUT_S = 3.0
        self.timeouts_seen = []
        self.acquire_errors = {}      # call number (1-based) -> exception
        self.capture_error = None     # raised on any capture
        self.deliver_forced = False   # a forced short capture still returns a frame
        self.release_error = None
        self.acquire_calls = 0
        self.opened = None
        self.shape = (72, 128, 3)
        self.frame_bgr = (255, 0, 0)

    def acquire_camera(self):
        self.acquire_calls += 1
        self.events.append("acquire")
        err = self.acquire_errors.get(self.acquire_calls)
        if err is not None:
            raise err
        self.refs += 1
        self.opened = 1.0
        return object()

    def release_camera(self):
        self.events.append("release")
        if self.release_error is not None:
            raise self.release_error
        if self.refs > 0:
            self.refs -= 1
        if self.refs == 0:
            self.opened = None

    def capture_frame(self):
        self.events.append("capture")
        self.timeouts_seen.append(self._CAPTURE_TIMEOUT_S)
        if self.capture_error is not None:
            raise self.capture_error
        if self._CAPTURE_TIMEOUT_S < 0.01 and not self.deliver_forced:
            raise TimeoutError("no frame in 0.001 s")
        frame = np.empty(self.shape, dtype=np.uint8)
        frame[:, :] = self.frame_bgr
        return frame

    def opened_at(self):
        return self.opened

    to_pil = staticmethod(camera.to_pil)


class Faults:
    """Records faulthandler.dump_traceback_later and its cancel."""

    def __init__(self, capsys):
        self.capsys = capsys
        self.arms = []
        self.cancels = 0
        self.armed = False
        self.out_before_first_arm = None

    def arm(self, timeout, repeat=False, file=None, exit=False):
        if not self.arms:
            self.out_before_first_arm = self.capsys.readouterr().out
        self.arms.append({"timeout": timeout, "exit": exit})
        self.armed = True

    def cancel(self):
        self.cancels += 1
        self.armed = False


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, capsys):
    logdir = tmp_path / "logs"
    logdir.mkdir()
    monkeypatch.setenv("BENDER_LOG_DIR", str(logdir))

    def no_subprocess(*a, **k):
        raise AssertionError(f"a test reached the real subprocess: {a!r}")

    monkeypatch.setattr(probe.subprocess, "run", no_subprocess)
    # The real repo root may hold a live session file on a dev box.
    monkeypatch.setattr(probe, "_session_file",
                        lambda: str(tmp_path / ".session_active.json"))
    faults = Faults(capsys)
    monkeypatch.setattr(probe.faulthandler, "dump_traceback_later", faults.arm)
    monkeypatch.setattr(probe.faulthandler, "cancel_dump_traceback_later",
                        faults.cancel)
    return faults


@pytest.fixture
def faults(env):
    return env


@pytest.fixture
def cam(monkeypatch):
    fake = FakeCam()
    monkeypatch.setattr(probe, "camera", fake)
    return fake


# ---------------------------------------------------------------------------
# Step 0
# ---------------------------------------------------------------------------

class TestStep0:
    def test_pass_prints_the_pillow_version(self, cam, capsys):
        import PIL
        assert probe.step0() == "PASS"
        out = capsys.readouterr().out
        assert PIL.__version__ in out
        assert "PASS" in out

    def test_fail_when_the_colours_are_swapped(self, cam, capsys):
        cam.to_pil = staticmethod(lambda f: camera.to_pil(f[..., ::-1].copy()))
        assert probe.step0() == "FAIL"
        assert "FAIL" in capsys.readouterr().out

    def test_fail_when_to_pil_raises(self, cam, capsys):
        def boom(frame):
            raise ValueError("bad raw mode")

        cam.to_pil = staticmethod(boom)
        assert probe.step0() == "FAIL"
        assert "bad raw mode" in capsys.readouterr().out

    def test_needs_no_camera(self, cam):
        probe.step0()
        assert cam.events == []


# ---------------------------------------------------------------------------
# Step 1
# ---------------------------------------------------------------------------

class TestStep1:
    def test_every_acquire_is_balanced(self, cam):
        assert probe.step1(cycles=7) == "DONE"
        assert cam.events.count("acquire") == 7
        assert cam.events.count("release") == 7
        assert cam.refs == 0
        assert "capture" not in cam.events

    def test_prints_the_numbers_at_each_interval_and_the_delta(
            self, cam, monkeypatch, capsys):
        monkeypatch.setattr(probe, "_SAMPLE_EVERY", 2)
        probe.step1(cycles=6)
        out = capsys.readouterr().out
        for n in (2, 4, 6):
            assert f"cycle {n}" in out
        for key in ("fds", "VmRSS", "Threads", "CmaFree"):
            assert key in out
        assert "delta" in out.lower()
        assert "cycle 2 to cycle 6" in out

    def test_a_count_that_is_not_a_multiple_still_gets_a_last_sample(
            self, cam, monkeypatch, capsys):
        monkeypatch.setattr(probe, "_SAMPLE_EVERY", 5)
        probe.step1(cycles=7)
        out = capsys.readouterr().out
        assert "cycle 5" in out
        assert "cycle 7" in out

    def test_one_sample_has_no_delta(self, cam, monkeypatch, capsys):
        monkeypatch.setattr(probe, "_SAMPLE_EVERY", 50)
        probe.step1(cycles=3)
        out = capsys.readouterr().out
        assert "cycle 3" in out
        assert "no delta" in out.lower()

    def test_a_refusal_at_cycle_1_raises_refused_and_takes_no_ref(self, cam):
        cam.acquire_errors[1] = RuntimeError("Device or resource busy")
        with pytest.raises(probe.Refused):
            probe.step1(cycles=5)
        assert "release" not in cam.events

    def test_a_failure_mid_run_is_fail_and_stays_balanced(self, cam, capsys):
        cam.acquire_errors[3] = RuntimeError("pipeline start failed")
        assert probe.step1(cycles=5) == "FAIL"
        out = capsys.readouterr().out
        assert "pipeline start failed" in out
        assert cam.events.count("acquire") == 3
        assert cam.events.count("release") == 2
        assert cam.refs == 0

    def test_a_release_error_is_fail_not_a_crash(self, cam, capsys):
        cam.release_error = RuntimeError("close hung")
        assert probe.step1(cycles=3) == "FAIL"
        assert "close hung" in capsys.readouterr().out

    def test_invents_no_threshold(self, cam, capsys):
        probe.step1(cycles=3)
        out = capsys.readouterr().out
        assert "PASS" not in out


class TestDeadline:
    """faulthandler.dump_traceback_later(exit=True) around every camera call.

    A hang in Picamera2() or close() is C++ and ignores Ctrl-C. The timer ends
    the process, and the kernel then frees the camera.
    """

    def test_constant(self):
        assert probe._CAMERA_DEADLINE_S == 30.0

    def test_step1_arms_and_cancels_around_every_acquire_and_release(
            self, cam, faults):
        probe.step1(cycles=4)
        assert len(faults.arms) == 8             # 4 cycles x (acquire + release)
        assert faults.cancels == 8
        assert not faults.armed
        for arm in faults.arms:
            assert arm["exit"] is True
            assert arm["timeout"] == probe._CAMERA_DEADLINE_S

    def test_step1_re_arms_each_cycle_not_once(self, cam, faults):
        probe.step1(cycles=3)
        assert len(faults.arms) > 3

    def test_every_camera_call_runs_with_the_timer_armed(self, cam, faults):
        seen = []

        def wrap(name, fn):
            def inner(*a, **k):
                seen.append((name, faults.armed))
                return fn(*a, **k)
            return inner

        for name in ("acquire_camera", "release_camera", "capture_frame"):
            setattr(cam, name, wrap(name, getattr(cam, name)))
        assert probe.step2() == "PASS"
        assert {n for n, _a in seen} == {"acquire_camera", "release_camera",
                                         "capture_frame"}
        assert all(armed for _n, armed in seen), seen

    def test_the_timer_is_cancelled_when_the_call_raises(self, cam, faults):
        cam.capture_error = RuntimeError("camera is not open")
        probe.step2()
        assert not faults.armed
        assert faults.cancels == len(faults.arms)

    def test_the_timer_is_cancelled_on_a_base_exception(self, cam, faults):
        cam.capture_error = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            probe.step2()
        assert not faults.armed
        assert faults.cancels == len(faults.arms)

    def test_the_timer_is_cancelled_when_release_raises(self, cam, faults):
        cam.release_error = RuntimeError("close hung")
        probe.step1(cycles=2)
        assert not faults.armed
        assert faults.cancels == len(faults.arms)

    def test_a_refused_acquire_cancels_the_timer(self, cam, faults):
        cam.acquire_errors[1] = RuntimeError("Device or resource busy")
        with pytest.raises(probe.Refused):
            probe.step1(cycles=2)
        assert not faults.armed

    @pytest.mark.parametrize("step", ["step1", "step2"])
    def test_one_line_says_what_the_deadline_does_before_arming(
            self, cam, faults, step):
        getattr(probe, step)()
        before = faults.out_before_first_arm
        assert before is not None
        assert "deadline" in before.lower()
        assert "end the process" in before
        assert "kernel" in before
        assert "30 s" in before

    def test_step0_and_step3_arm_nothing(self, cam, faults, monkeypatch):
        probe.step0()
        systemctl_says(monkeypatch, "0")
        probe.step3()
        assert faults.arms == []


class TestProcReaders:
    def test_read_the_live_process(self):
        assert probe._open_fds() > 0
        assert probe._proc_kb("/proc/self/status", "VmRSS") > 0
        assert probe._proc_kb("/proc/self/status", "Threads") >= 1

    def test_a_missing_key_gives_none(self):
        assert probe._proc_kb("/proc/self/status", "NoSuchKey") is None

    def test_a_missing_file_gives_none(self, tmp_path):
        assert probe._proc_kb(str(tmp_path / "nope"), "VmRSS") is None

    def test_a_missing_cmafree_prints_n_a(self, cam, monkeypatch, capsys):
        real = probe._proc_kb
        monkeypatch.setattr(
            probe, "_proc_kb",
            lambda path, key: None if key == "CmaFree" else real(path, key))
        monkeypatch.setattr(probe, "_SAMPLE_EVERY", 1)
        assert probe.step1(cycles=2) == "DONE"
        assert "n/a" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Step 2
# ---------------------------------------------------------------------------

class TestStep2:
    def test_pass_reports_the_frame_shape(self, cam, capsys):
        assert probe.step2() == "PASS"
        out = capsys.readouterr().out
        assert "PASS" in out
        assert str(cam.shape) in out

    def test_the_forced_capture_ran_at_a_short_timeout_then_a_normal_one(
            self, cam):
        probe.step2()
        assert cam.timeouts_seen[0] == 0.001
        assert cam.timeouts_seen[-1] == 3.0
        assert cam._CAPTURE_TIMEOUT_S == 3.0

    def test_closes_and_reopens_between_the_two_captures(self, cam):
        probe.step2()
        assert cam.events == ["acquire", "capture", "release",
                              "acquire", "capture", "release"]

    def test_every_acquire_is_balanced(self, cam):
        probe.step2()
        assert cam.events.count("acquire") == cam.events.count("release")
        assert cam.refs == 0

    def test_finally_restores_the_timeout_when_capture_raises_something_else(
            self, cam, capsys):
        cam.capture_error = RuntimeError("camera is not open")
        assert probe.step2() == "FAIL"
        assert cam._CAPTURE_TIMEOUT_S == 3.0
        assert "camera is not open" in capsys.readouterr().out
        assert cam.refs == 0
        assert cam.events.count("acquire") == cam.events.count("release")

    def test_finally_restores_the_timeout_on_a_base_exception(self, cam):
        cam.capture_error = KeyboardInterrupt()
        with pytest.raises(KeyboardInterrupt):
            probe.step2()
        assert cam._CAPTURE_TIMEOUT_S == 3.0
        assert cam.refs == 0

    def test_inconclusive_when_no_timeout_ever_comes(self, cam, capsys):
        cam.deliver_forced = True
        assert probe.step2() == "INCONCLUSIVE"
        out = capsys.readouterr().out
        assert "INCONCLUSIVE" in out
        assert "PASS" not in out
        assert cam.timeouts_seen.count(0.001) == probe._FORCED_ATTEMPTS
        assert cam.refs == 0
        assert cam._CAPTURE_TIMEOUT_S == 3.0

    def test_a_retry_that_times_out_still_passes(self, cam):
        calls = {"n": 0}
        real = cam.capture_frame

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                cam.deliver_forced = True
            else:
                cam.deliver_forced = False
            return real()

        cam.capture_frame = flaky
        assert probe.step2() == "PASS"

    def test_fail_when_the_second_open_fails(self, cam, capsys):
        cam.acquire_errors[2] = RuntimeError("would not reopen")
        assert probe.step2() == "FAIL"
        assert "would not reopen" in capsys.readouterr().out
        assert cam.refs == 0

    def test_fail_when_the_normal_capture_after_reopen_fails(self, cam, capsys):
        real = cam.capture_frame

        def second_fails():
            if cam._CAPTURE_TIMEOUT_S >= 0.01:
                raise TimeoutError("still no frame")
            return real()

        cam.capture_frame = second_fails
        assert probe.step2() == "FAIL"
        assert "still no frame" in capsys.readouterr().out
        assert cam.refs == 0

    def test_a_refusal_raises_refused(self, cam):
        cam.acquire_errors[1] = RuntimeError("Device or resource busy")
        with pytest.raises(probe.Refused):
            probe.step2()
        assert "release" not in cam.events

    def test_a_release_error_is_fail_not_a_crash(self, cam, capsys):
        cam.release_error = RuntimeError("close hung")
        assert probe.step2() == "FAIL"
        assert cam._CAPTURE_TIMEOUT_S == 3.0


# ---------------------------------------------------------------------------
# Step 3
# ---------------------------------------------------------------------------

class Completed:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""


class Sim:
    """A fake clock, a fake /proc/<pid>/stat and a fake camera fd count.

    The worker "opens" the camera at the sleep during which the warm note first
    exists, holds it 15 s, then closes it. `pre_open` starts with the camera
    already held. `opens_camera=False` is a worker that takes the note and
    fails to open.
    """

    HOLD = 15.0

    def __init__(self, monkeypatch, ticks=(), takes_note=True,
                 opens_camera=True, pre_open=False, stuck_open=False):
        self.t = 1000.0
        self.sleeps = []
        self.ticks = list(ticks)
        self.takes_note = takes_note
        self.opens_camera = opens_camera
        self.pre_open = pre_open
        self.stuck_open = stuck_open
        self.hold_start = None
        self.link_reads = []
        monkeypatch.setattr(probe, "_now", lambda: self.t)
        monkeypatch.setattr(probe, "_sleep", self.sleep)
        monkeypatch.setattr(probe, "_cpu_ticks", self.cpu_ticks)
        monkeypatch.setattr(probe, "_camera_links", self.links)
        monkeypatch.setattr(probe, "_clk_tck", lambda: 100)

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
        note = os.path.join(probe._frames_dir(), ".queue", "warm")
        if os.path.exists(note):
            if self.opens_camera and self.hold_start is None:
                self.hold_start = self.t
            if self.takes_note:
                os.unlink(note)

    def cpu_ticks(self, pid):
        return self.ticks.pop(0)

    def links(self, pid):
        if self.stuck_open and self.hold_start is not None:
            n = 1
        elif self.hold_start is not None and self.t < self.hold_start + self.HOLD:
            n = 1
        elif self.pre_open and self.hold_start is None:
            n = 1
        else:
            n = 0
        self.link_reads.append((self.t, n))
        return n


def systemctl_says(monkeypatch, stdout, calls=None):
    def fake_run(args, **kw):
        if calls is not None:
            calls.append(list(args))
        if isinstance(stdout, BaseException):
            raise stdout
        return Completed(stdout)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)


def make_queue():
    q = os.path.join(probe._frames_dir(), ".queue")
    os.makedirs(q, exist_ok=True)
    return q


TICKS = [100, 120, 130, 430]   # baseline 20 ticks / 10 s, hold 300 ticks / 14 s


class TestStep3:
    def test_skips_when_systemctl_gives_no_pid(self, cam, monkeypatch, capsys):
        systemctl_says(monkeypatch, "0\n")
        assert probe.step3() == "SKIP"
        out = capsys.readouterr().out
        assert "bender-web" in out
        assert "skip" in out.lower()

    def test_skips_when_systemctl_is_missing(self, cam, monkeypatch):
        systemctl_says(monkeypatch, FileNotFoundError("systemctl"))
        assert probe.step3() == "SKIP"

    def test_skips_when_systemctl_times_out(self, cam, monkeypatch):
        systemctl_says(monkeypatch,
                       subprocess.TimeoutExpired("systemctl", 5))
        assert probe.step3() == "SKIP"

    def test_skips_on_garbage_output(self, cam, monkeypatch):
        systemctl_says(monkeypatch, "not a number")
        assert probe.step3() == "SKIP"

    def test_skip_writes_no_note(self, cam, monkeypatch):
        systemctl_says(monkeypatch, "0")
        probe.step3()
        assert not os.path.exists(os.path.join(probe._frames_dir(), ".queue"))

    def test_only_ever_asks_systemctl_to_show(self, cam, monkeypatch):
        calls = []
        systemctl_says(monkeypatch, "0", calls)
        probe.step3()
        assert calls == [["systemctl", "show", "-p", "MainPID", "--value",
                          "bender-web"]]

    def test_skips_when_the_queue_folder_is_missing(self, cam, monkeypatch,
                                                    capsys):
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch)
        assert probe.step3() == "SKIP"
        assert "queue" in capsys.readouterr().out.lower()

    def test_prints_baseline_hold_and_delta_when_a_hold_was_seen(
            self, cam, monkeypatch, capsys):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch, ticks=TICKS)
        assert probe.step3() == "DONE"
        out = capsys.readouterr().out
        assert "2.0" in out
        assert "21.4" in out
        assert "19.4" in out
        assert "4242" in out
        assert "with the camera held" in out

    def test_the_baseline_runs_10_s_and_the_window_14_s(self, cam, monkeypatch):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        sim = Sim(monkeypatch, ticks=TICKS)
        probe.step3()
        assert sim.sleeps[0] == 10
        assert sum(sim.sleeps[1:5]) == 14

    def test_the_camera_is_looked_at_before_during_and_after(
            self, cam, monkeypatch):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        sim = Sim(monkeypatch, ticks=TICKS)
        probe.step3()
        times = [t for t, _n in sim.link_reads]
        start = 1000.0
        assert times[0] == start                         # before the baseline
        during = [n for t, n in sim.link_reads if start + 10 < t <= start + 24]
        assert len(during) >= 2 and max(during) >= 1     # in the window
        assert sim.link_reads[-1][1] == 0                # after: released

    def test_the_warm_note_comes_after_the_baseline(self, cam, monkeypatch):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        sim = Sim(monkeypatch, ticks=[0, 0, 0, 0], takes_note=False)
        seen = []
        real_sleep = sim.sleep

        def spy(s):
            seen.append(os.path.exists(
                os.path.join(probe._frames_dir(), ".queue", "warm")))
            real_sleep(s)

        monkeypatch.setattr(probe, "_sleep", spy)
        probe.step3()
        assert seen[0] is False       # the baseline sleep: no note yet
        assert seen[1] is True        # the window: the note is there

    def test_the_warm_note_is_private_and_needs_no_enabled_flag(
            self, cam, monkeypatch):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch, ticks=[0, 0, 0, 0], takes_note=False)
        probe.step3()
        path = os.path.join(probe._frames_dir(), ".queue", "warm")
        assert os.path.exists(path)
        assert (os.stat(path).st_mode & 0o777) == 0o600

    def test_inconclusive_when_nobody_takes_the_note(self, cam, monkeypatch,
                                                     capsys):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch, ticks=TICKS, takes_note=False, opens_camera=False)
        assert probe.step3() == "INCONCLUSIVE"
        assert "not taken" in capsys.readouterr().out.lower()

    def test_inconclusive_when_the_note_is_taken_but_no_camera_opens(
            self, cam, monkeypatch, capsys):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch, ticks=TICKS, opens_camera=False)
        assert probe.step3() == "INCONCLUSIVE"
        out = capsys.readouterr().out
        assert "no camera" in out.lower()
        assert "with the camera held" not in out
        assert "DONE" not in out

    def test_inconclusive_when_the_camera_is_already_open_at_baseline(
            self, cam, monkeypatch, capsys):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        sim = Sim(monkeypatch, ticks=TICKS, pre_open=True)
        assert probe.step3() == "INCONCLUSIVE"
        out = capsys.readouterr().out
        assert "already" in out.lower()
        assert "See Room" in out
        # nothing was measured and no note was left
        assert sim.sleeps == []
        assert not os.path.exists(
            os.path.join(probe._frames_dir(), ".queue", "warm"))

    def test_fail_when_the_worker_never_lets_go(self, cam, monkeypatch,
                                                capsys):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch, ticks=TICKS, stuck_open=True)
        assert probe.step3() == "FAIL"
        assert "still" in capsys.readouterr().out.lower()

    def test_waits_for_the_release_by_polling_not_by_a_fixed_sleep(
            self, cam, monkeypatch):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        sim = Sim(monkeypatch, ticks=TICKS)
        probe.step3()
        tail = sim.sleeps[5:]
        assert tail, "no wait after the window"
        assert sum(tail) < probe._RELEASE_WAIT_S   # stopped when it released

    def test_an_unreadable_fd_folder_is_inconclusive(self, cam, monkeypatch,
                                                     capsys):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch, ticks=TICKS)

        def denied(pid):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(probe, "_camera_links", denied)
        assert probe.step3() == "INCONCLUSIVE"
        assert "/proc/4242/fd" in capsys.readouterr().out

    def test_fail_when_this_process_holds_the_camera(self, cam, monkeypatch,
                                                     capsys):
        make_queue()
        cam.opened = 5.0
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch, ticks=[0, 0, 0, 0])
        assert probe.step3() == "FAIL"
        assert "holds the camera" in capsys.readouterr().out

    def test_a_vanished_process_is_skip_not_a_crash(self, cam, monkeypatch,
                                                    capsys):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch)

        def gone(pid):
            raise FileNotFoundError(f"/proc/{pid}/stat")

        monkeypatch.setattr(probe, "_cpu_ticks", gone)
        assert probe.step3() == "SKIP"
        assert "4242" in capsys.readouterr().out

    def test_takes_no_camera(self, cam, monkeypatch):
        make_queue()
        systemctl_says(monkeypatch, "4242\n")
        Sim(monkeypatch, ticks=TICKS)
        probe.step3()
        assert cam.events == []


class TestCameraLinks:
    def make_fd_tree(self, tmp_path, targets):
        fd = tmp_path / "4242" / "fd"
        fd.mkdir(parents=True)
        for i, t in enumerate(targets):
            os.symlink(t, fd / str(i))
        return tmp_path

    def test_counts_video_and_media_devices_only(self, tmp_path, monkeypatch):
        root = self.make_fd_tree(tmp_path, [
            "/dev/video0", "/dev/media1", "/dev/video19", "/dev/null",
            "socket:[123]", "/home/pi/logs/video.log", "anon_inode:[eventfd]"])
        monkeypatch.setattr(probe, "_PROC", str(root))
        assert probe._camera_links(4242) == 3

    def test_none_open_is_zero(self, tmp_path, monkeypatch):
        root = self.make_fd_tree(tmp_path, ["/dev/null", "socket:[1]"])
        monkeypatch.setattr(probe, "_PROC", str(root))
        assert probe._camera_links(4242) == 0

    def test_a_vanishing_fd_is_skipped(self, tmp_path, monkeypatch):
        root = self.make_fd_tree(tmp_path, ["/dev/video0", "/dev/video1"])
        monkeypatch.setattr(probe, "_PROC", str(root))
        real = os.readlink

        def flaky(path):
            if path.endswith("/0"):
                raise FileNotFoundError(path)
            return real(path)

        monkeypatch.setattr(probe.os, "readlink", flaky)
        assert probe._camera_links(4242) == 1

    def test_a_missing_process_raises_oserror(self, tmp_path, monkeypatch):
        monkeypatch.setattr(probe, "_PROC", str(tmp_path))
        with pytest.raises(OSError):
            probe._camera_links(4242)


class TestStep3Constants:
    def test_the_window_ends_before_the_workers_hold_does(self):
        import turn_frames
        assert probe._HOLD_WINDOW_S < turn_frames._HOLD_S

    def test_the_note_check_leaves_the_worker_several_polls(self):
        import turn_frames
        assert probe._NOTE_CHECK_S >= 3 * turn_frames._POLL_S
        assert probe._NOTE_CHECK_S < turn_frames._STALE_S

    def test_the_release_wait_covers_the_rest_of_the_hold(self):
        """The worker releases at about note + poll + open + _HOLD_S. The
        window ends _HOLD_S - _HOLD_WINDOW_S before that, and an open can take
        a few seconds."""
        import turn_frames
        rest = turn_frames._HOLD_S - probe._HOLD_WINDOW_S + turn_frames._POLL_S
        assert probe._RELEASE_WAIT_S >= rest + 5


class TestFramesDirMatchesTurnFrames:
    def test_with_the_env_var_set(self, tmp_path, monkeypatch):
        import turn_frames
        monkeypatch.setenv("BENDER_LOG_DIR", str(tmp_path / "x"))
        assert probe._frames_dir() == turn_frames.frames_dir()

    def test_with_the_env_var_unset(self, monkeypatch):
        import turn_frames
        monkeypatch.delenv("BENDER_LOG_DIR", raising=False)
        assert probe._frames_dir() == turn_frames.frames_dir()

    def test_with_the_env_var_empty(self, monkeypatch):
        import turn_frames
        monkeypatch.setenv("BENDER_LOG_DIR", "")
        assert probe._frames_dir() == turn_frames.frames_dir()


class TestCpuTicks:
    def test_reads_utime_and_stime_past_a_name_with_spaces_and_parens(
            self, tmp_path, monkeypatch):
        fields = ["S"] + ["0"] * 10 + ["70", "30"] + ["0"] * 30
        stat = tmp_path / "stat"
        stat.write_text("4242 (my (odd) name) " + " ".join(fields) + "\n")
        monkeypatch.setattr(probe, "_PROC", str(tmp_path))
        os.makedirs(tmp_path / "4242", exist_ok=True)
        (tmp_path / "4242" / "stat").write_text(stat.read_text())
        assert probe._cpu_ticks(4242) == 100

    def test_reads_this_process(self):
        assert probe._cpu_ticks(os.getpid()) >= 0


# ---------------------------------------------------------------------------
# main and the command line
# ---------------------------------------------------------------------------

class TestMain:
    def stub_steps(self, monkeypatch, results):
        ran = []

        def make(n):
            def step(*a, **k):
                ran.append((n, a, k))
                r = results[n]
                if isinstance(r, BaseException):
                    raise r
                if callable(r):
                    r = r()
                return r
            return step

        monkeypatch.setattr(probe, "step0", make(0))
        monkeypatch.setattr(probe, "step1", make(1))
        monkeypatch.setattr(probe, "step2", make(2))
        monkeypatch.setattr(probe, "step3", make(3))
        return ran

    def test_help_exits_0(self, capsys):
        with pytest.raises(SystemExit) as exc:
            probe.main(["--help"])
        assert exc.value.code == 0
        assert "--cycles" in capsys.readouterr().out

    def test_help_exits_0_as_a_script(self, tmp_path):
        env = dict(os.environ, BENDER_LOG_DIR=str(tmp_path))
        out = REAL_RUN([sys.executable, "-I", SCRIPT, "--help"],
                             env=env, capture_output=True, text=True,
                             timeout=60)
        assert out.returncode == 0, out.stderr
        assert "--step" in out.stdout

    def test_steps_run_in_order_and_all_pass_exits_0(self, monkeypatch):
        ran = self.stub_steps(monkeypatch, {0: "PASS", 1: "DONE", 2: "PASS",
                                            3: "DONE"})
        assert probe.main([]) == 0
        assert [n for n, _a, _k in ran] == [0, 1, 2, 3]

    def test_step_n_runs_one_step(self, monkeypatch):
        ran = self.stub_steps(monkeypatch, {0: "PASS", 1: "DONE", 2: "PASS",
                                            3: "DONE"})
        assert probe.main(["--step", "2"]) == 0
        assert [n for n, _a, _k in ran] == [2]

    def test_cycles_overrides_300(self, monkeypatch):
        ran = self.stub_steps(monkeypatch, {0: "PASS", 1: "DONE", 2: "PASS",
                                            3: "DONE"})
        probe.main(["--step", "1", "--cycles", "12"])
        assert ran[0][1] == (12,) or ran[0][2] == {"cycles": 12}

    def test_default_cycles_is_300(self, monkeypatch):
        ran = self.stub_steps(monkeypatch, {0: "PASS", 1: "DONE", 2: "PASS",
                                            3: "DONE"})
        probe.main(["--step", "1"])
        assert ran[0][1] == (300,) or ran[0][2] == {"cycles": 300}

    @pytest.mark.parametrize("bad", ["0", "-5", "abc"])
    def test_bad_cycles_is_a_usage_error(self, bad, monkeypatch):
        self.stub_steps(monkeypatch, {0: "PASS", 1: "DONE", 2: "PASS",
                                      3: "DONE"})
        with pytest.raises(SystemExit) as exc:
            probe.main(["--step", "1", "--cycles", bad])
        assert exc.value.code == 2

    @pytest.mark.parametrize("result", ["FAIL", "INCONCLUSIVE"])
    def test_a_non_pass_exits_1(self, monkeypatch, result):
        self.stub_steps(monkeypatch, {0: "PASS", 1: "DONE", 2: result,
                                      3: "DONE"})
        assert probe.main([]) == 1

    def test_a_skip_is_not_a_failure(self, monkeypatch):
        self.stub_steps(monkeypatch, {0: "PASS", 1: "DONE", 2: "PASS",
                                      3: "SKIP"})
        assert probe.main([]) == 0

    def test_a_failing_step_does_not_stop_the_later_ones(self, monkeypatch):
        ran = self.stub_steps(monkeypatch, {0: "FAIL", 1: "DONE", 2: "PASS",
                                            3: "DONE"})
        assert probe.main([]) == 1
        assert [n for n, _a, _k in ran] == [0, 1, 2, 3]

    ALL_PASS = {0: "PASS", 1: "DONE", 2: "PASS", 3: "DONE"}

    def lines(self, capsys):
        return [x for x in capsys.readouterr().out.splitlines() if x.strip()]

    def test_a_busy_refusal_is_one_line_naming_see_room_and_the_text(
            self, monkeypatch, capsys):
        ran = self.stub_steps(monkeypatch, {
            **self.ALL_PASS,
            1: probe.Refused(RuntimeError(
                "Failed to acquire camera: Device or resource busy"))})
        code = probe.main([])
        lines = self.lines(capsys)
        assert code != 0
        assert len(lines) == 1, lines
        assert "See Room" in lines[0]
        assert "20 s" in lines[0]
        assert "resource busy" in lines[0]
        assert [n for n, _a, _k in ran] == [0, 1]   # stops at the refusal

    def test_a_camera_busy_names_see_room_too(self, monkeypatch, capsys):
        self.stub_steps(monkeypatch, {
            **self.ALL_PASS, 2: probe.Refused(camera.CameraBusy("locked"))})
        assert probe.main([]) != 0
        lines = self.lines(capsys)
        assert len(lines) == 1, lines
        assert "See Room" in lines[0]
        assert "locked" in lines[0]

    def test_some_other_first_acquire_error_does_not_blame_see_room(
            self, monkeypatch, capsys):
        self.stub_steps(monkeypatch, {
            **self.ALL_PASS,
            1: probe.Refused(RuntimeError(
                "picamera2 is not available. Install it or run on a "
                "Raspberry Pi."))})
        code = probe.main([])
        lines = self.lines(capsys)
        assert code != 0
        assert len(lines) == 1, lines
        assert "See Room" not in lines[0]
        assert "not available" in lines[0]

    def test_a_non_runtime_first_acquire_error_does_not_blame_see_room(
            self, monkeypatch, capsys):
        self.stub_steps(monkeypatch, {
            **self.ALL_PASS, 1: probe.Refused(ImportError("no libcamera"))})
        assert probe.main([]) != 0
        lines = self.lines(capsys)
        assert len(lines) == 1, lines
        assert "See Room" not in lines[0]
        assert "no libcamera" in lines[0]

    def test_a_real_first_acquire_failure_goes_through_the_same_path(
            self, cam, monkeypatch, capsys):
        cam.acquire_errors[1] = RuntimeError("Device or resource busy")
        monkeypatch.setattr(probe, "step0", lambda: "PASS")
        assert probe.main(["--step", "2"]) == 2
        lines = self.lines(capsys)
        assert len(lines) == 1 and "See Room" in lines[0]

    def test_an_unexpected_crash_in_a_step_is_fail_not_a_traceback(
            self, monkeypatch, capsys):
        self.stub_steps(monkeypatch, {0: ValueError("oops"), 1: "DONE",
                                      2: "PASS", 3: "DONE"})
        assert probe.main(["--step", "0"]) == 1
        assert "oops" in capsys.readouterr().out


class TestSessionGuard:
    ALL_PASS = {0: "PASS", 1: "DONE", 2: "PASS", 3: "DONE"}

    def stub(self, monkeypatch):
        return TestMain().stub_steps(monkeypatch, dict(self.ALL_PASS))

    def live_session(self):
        open(probe._session_file(), "w").close()

    def test_the_real_path_is_the_one_config_uses(self):
        from config import cfg
        assert REAL_SESSION_FILE() == cfg.session_file
        assert REAL_SESSION_FILE() == os.path.join(probe._BASE_DIR,
                                                   ".session_active.json")

    def test_a_live_session_refuses_the_camera_steps(self, monkeypatch,
                                                     capsys):
        ran = self.stub(monkeypatch)
        self.live_session()
        code = probe.main([])
        out = capsys.readouterr().out
        assert code != 0
        assert [n for n, _a, _k in ran] == [0]    # step 0 needs no guard
        assert "--force" in out
        assert "session" in out.lower()

    @pytest.mark.parametrize("step", [1, 2, 3])
    def test_each_camera_step_is_guarded(self, monkeypatch, capsys, step):
        ran = self.stub(monkeypatch)
        self.live_session()
        assert probe.main(["--step", str(step)]) != 0
        assert ran == []

    def test_step_0_alone_ignores_a_live_session(self, monkeypatch):
        ran = self.stub(monkeypatch)
        self.live_session()
        assert probe.main(["--step", "0"]) == 0
        assert [n for n, _a, _k in ran] == [0]

    def test_force_overrides_the_guard(self, monkeypatch):
        ran = self.stub(monkeypatch)
        self.live_session()
        assert probe.main(["--force"]) == 0
        assert [n for n, _a, _k in ran] == [0, 1, 2, 3]

    def test_no_session_file_runs_everything(self, monkeypatch):
        ran = self.stub(monkeypatch)
        assert probe.main([]) == 0
        assert [n for n, _a, _k in ran] == [0, 1, 2, 3]

    def test_it_looks_again_at_the_start_of_each_step(self, monkeypatch,
                                                      capsys):
        ran = TestMain().stub_steps(monkeypatch, {
            0: "PASS", 1: lambda: (open(probe._session_file(), "w").close(),
                                   "DONE")[1],
            2: "PASS", 3: "DONE"})
        code = probe.main([])
        assert code != 0
        assert [n for n, _a, _k in ran] == [0, 1]   # a child arrived after step 1
        assert "session" in capsys.readouterr().out.lower()

    def test_the_refusal_is_one_line(self, monkeypatch, capsys):
        self.stub(monkeypatch)
        self.live_session()
        probe.main(["--step", "1"])
        lines = [x for x in capsys.readouterr().out.splitlines() if x.strip()]
        assert len(lines) == 1, lines

    def test_help_mentions_force(self, capsys):
        with pytest.raises(SystemExit):
            probe.main(["--help"])
        assert "--force" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

class TestImport:
    def test_importing_does_not_pull_in_picamera2_or_pil(self, tmp_path):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import probe_turn_frames\n"
            "bad = [m for m in ('picamera2', 'PIL') if m in sys.modules]\n"
            "assert not bad, bad\n"
            "print('ok')\n" % os.path.join(REPO, "scripts")
        )
        env = dict(os.environ, BENDER_LOG_DIR=str(tmp_path))
        out = REAL_RUN([sys.executable, "-I", "-c", code], env=env,
                       capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "ok"

    def test_script_run_imports_no_config_and_no_picamera2(self, tmp_path):
        """Step 0 runs for real here: it needs Pillow and numpy, no camera."""
        code = (
            "import runpy, sys\n"
            "sys.argv = ['probe_turn_frames.py', '--step', '0']\n"
            "try:\n"
            "    runpy.run_path(%r, run_name='__main__')\n"
            "except SystemExit as e:\n"
            "    code = e.code\n"
            "bad = [m for m in ('picamera2', 'config', 'turn_frames')\n"
            "       if m in sys.modules]\n"
            "assert not bad, bad\n"
            "assert code == 0, code\n"
            "print('ok')\n" % SCRIPT
        )
        env = dict(os.environ, BENDER_LOG_DIR=str(tmp_path))
        out = REAL_RUN([sys.executable, "-I", "-c", code], env=env,
                       capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr + out.stdout
        assert out.stdout.strip().endswith("ok")
        assert not (tmp_path / "bender.log").exists()

    def test_importing_creates_nothing(self, tmp_path):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import probe_turn_frames\n" % os.path.join(REPO, "scripts")
        )
        fresh = tmp_path / "fresh"
        env = dict(os.environ, BENDER_LOG_DIR=str(fresh))
        REAL_RUN([sys.executable, "-I", "-c", code], env=env,
                       capture_output=True, text=True, timeout=60, check=True)
        assert not (fresh / "frames").exists()

    def test_constants(self):
        assert probe._SAMPLE_EVERY == 50
        assert probe._DEFAULT_CYCLES == 300
        assert probe._FORCED_TIMEOUT_S == 0.001
