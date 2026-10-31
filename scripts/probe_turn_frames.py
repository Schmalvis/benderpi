#!/usr/bin/env python3
"""Device check for turn frames. Standalone. Prints plain lines and exits.

WHY
---
Turn frames move the camera into bender-web, where a worker thread opens it,
holds it 15 s after the last request and releases it. The unit tests use a fake
camera. They cannot say whether the real camera leaks over many open and close
cycles, whether the recovery path after a hung capture works, or what the held
camera costs in CPU. This script answers those three on the device. It also
closes one gap in the tests: camera.to_pil was tested only on Pillow 11.1.0,
and the device has Pillow 12.1.1.

USAGE (on the device, after an approved deploy)
    venv/bin/python scripts/probe_turn_frames.py              # steps 0-3 in order
    venv/bin/python scripts/probe_turn_frames.py --step 2     # one step
    venv/bin/python scripts/probe_turn_frames.py --cycles 50  # step 1 with 50 cycles
    venv/bin/python scripts/probe_turn_frames.py --force      # ignore a live session

Run it with bender-converse running and See Room CLOSED. The camera is a
single-owner device. If See Room is open, or the bender-web worker still holds
the camera, step 1 and step 2 are refused. Close See Room, or wait 20 s, then
run it again.

It refuses steps 1-3, with one line and exit 2, while <repo>/.session_active.json
exists, because camera activity near Piper may drop audio and a child may be
talking. It checks again at the start of each step. --force overrides it.

DEADLINE
Picamera2(), start() and close() are C++ calls with no timeout, and a hang there
ignores Ctrl-C. Every camera call in steps 1 and 2 runs under
faulthandler.dump_traceback_later(30 s, exit=True), re-armed for each call and
cancelled in a finally. A hang prints the stack and ends the process, and the
kernel then frees the camera.

STEPS
    0  Print the Pillow version. Run camera.to_pil on a pure blue BGR array.
       PASS if the pixels come out blue. Needs no camera.
    1  300 acquire and release cycles with no capture. At each 50, print the
       cycle number, open fd count, VmRSS, thread count and CmaFree. At the end
       print the change from the first sample to the last. It prints numbers and
       invents no pass threshold. Read them.
    2  The real recovery path. Acquire, force camera._CAPTURE_TIMEOUT_S to
       0.001, call capture_frame(), expect TimeoutError, restore the value,
       release (which closes), acquire again, capture a normal frame, release.
       PASS with the frame shape. If the short timeout delivers a frame, it
       retries a few times, then reports INCONCLUSIVE, never PASS.
    3  CPU of bender-web during a 15 s camera hold. Read a 10 s baseline, then
       refresh <frames>/.queue/warm so the bender-web worker opens and holds the
       camera, whatever turn_frames_enabled says. Print CPU percent of one core
       over 14 s, and the delta from the baseline. It counts bender-web's
       /dev/video* and /dev/media* fds before the baseline, three times in the
       window and after, and prints DONE only when a hold was seen and ended. It
       reports INCONCLUSIVE if the camera was already open at the baseline, or
       if none opened. It skips if bender-web is not running.

WHAT IT DOES NOT DO
    - It does not stop or restart any service. Step 3 runs one read-only
      `systemctl show`.
    - It reads no .env and imports no bender config.
    - It does not run the listening test. Steps 4-6 of the plan's on-device
      test stay by hand: the night image, one session with Piper talking and
      See Room closed (listen for dropouts), and See Room open during a session.
    - It does not decide pass or fail on memory, fds, threads or CPU. A human
      reads those numbers.

EXIT CODE
    0  no step failed. 1  a step reported FAIL or INCONCLUSIVE. 2  usage error,
    a live session, or the first camera acquire failed.

Importing this module imports no picamera2 and no PIL. The camera module
imports picamera2 itself, and only when a camera is opened. Run as a script, it
also imports no bender config.
"""
from __future__ import annotations

import argparse
import faulthandler
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager

# The probe has to work when the service is wedged, so it imports no config
# (which reads .env). camera.py needs only the logger, and logger._init_root()
# imports config on first use. Marking the logger initialised skips that. It
# also keeps camera's INFO lines (3 per cycle, 900 in step 1) off the output and
# out of logs/bender.log. Warnings still reach stderr. The `logger._initialised`
# flag is private; test_script_run_imports_no_config_and_no_picamera2 fails if
# a change to logger.py breaks this.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))



def _quiet_logging() -> None:
    import logger as bender_logger

    bender_logger._initialised = True
    root_log = logging.getLogger("bender")
    root_log.setLevel(logging.WARNING)
    root_log.addHandler(logging.StreamHandler(sys.stderr))


# Only when run as a script. An import from the test suite must not change the
# logging of every other test.
if __name__ == "__main__":
    _quiet_logging()

import camera  # noqa: E402

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROC = "/proc"

_DEFAULT_CYCLES = 300
_SAMPLE_EVERY = 50
_FORCED_TIMEOUT_S = 0.001
_FORCED_ATTEMPTS = 5

_BASELINE_S = 10.0
_HOLD_WINDOW_S = 14.0
_NOTE_CHECK_S = 1.0       # the worker polls every 0.2 s, so it takes a note in 1 s
_WINDOW_SAMPLES_S = (_NOTE_CHECK_S, 7.0, 13.0)  # when to look for the camera fd
# The worker releases at about note + poll (0.2 s) + camera open + 15 s. The
# window ends 14 s after the note, so the rest of the hold is about 1.2 s plus
# the open time. This is the MOST the probe waits for it. It stops as soon as
# the fd is gone.
_RELEASE_WAIT_S = 10.0
_RELEASE_POLL_S = 0.5
_SYSTEMCTL_TIMEOUT_S = 5

# faulthandler ends the process if one camera call takes longer than this.
# Picamera2(), start() and close() are C++ with no timeout (see camera.py).
_CAMERA_DEADLINE_S = 30.0

_REFUSED_HINT = (
    "Likely cause: See Room is open, or the bender-web worker is holding the "
    "camera. Close See Room, or wait 20 s, then run it again."
)


class Refused(Exception):
    """The first acquire failed. This process took no camera ref."""

    @property
    def cause(self) -> BaseException:
        return self.args[0]

    def is_camera_refusal(self) -> bool:
        """True only for a failure that means another holder has the camera."""
        cause = self.cause
        busy = getattr(camera, "CameraBusy", ())
        if isinstance(cause, busy):
            return True
        text = str(cause).lower()
        return (isinstance(cause, RuntimeError)
                and ("busy" in text or "acquire" in text))

    def line(self) -> str:
        """One line for the owner."""
        if self.is_camera_refusal():
            return f"REFUSED: {_err(self.cause)}. {_REFUSED_HINT}"
        return (f"CAMERA ERROR: {_err(self.cause)}. This is not a camera "
                "refusal, so fix this first.")


def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


# Patchable by tests, so no test sleeps or reads a real clock.
_now = time.monotonic


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _session_file() -> str:
    """cfg.session_file, worked out without config. bender-converse writes it
    for the life of a conversation."""
    return os.path.join(_BASE_DIR, ".session_active.json")


def _frames_dir() -> str:
    """The same path turn_frames.frames_dir() gives. Not imported: it needs config."""
    base = os.environ.get("BENDER_LOG_DIR") or os.path.join(_BASE_DIR, "logs")
    return os.path.join(base, "frames")


# ---------------------------------------------------------------------------
# Camera helpers
# ---------------------------------------------------------------------------

def _acquire_first() -> None:
    """The first acquire of a step. A failure here means the camera is taken."""
    try:
        camera.acquire_camera()
    except Exception as exc:
        raise Refused(exc) from exc


@contextmanager
def _deadline():
    """End the process if the body takes over _CAMERA_DEADLINE_S.

    A hang in C++ ignores Ctrl-C, so only this can stop the probe holding the
    camera. exit=True ends the process, and the kernel then frees the camera.
    Cancelled in a finally. One timer exists at a time, so each camera call
    re-arms it.
    """
    faulthandler.dump_traceback_later(_CAMERA_DEADLINE_S, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def _announce_deadline() -> None:
    _say(f"  Deadline: each camera call gets {_CAMERA_DEADLINE_S:.0f} s. A hang "
         "will end the process, and the kernel then frees the camera.")


@contextmanager
def _held(first: bool = False):
    """Acquire, and always release. A failed acquire takes no ref, so no release."""
    with _deadline():
        if first:
            _acquire_first()
        else:
            camera.acquire_camera()
    try:
        yield
    finally:
        with _deadline():
            camera.release_camera()


# ---------------------------------------------------------------------------
# Step 0: Pillow and to_pil
# ---------------------------------------------------------------------------

def step0() -> str:
    _say("STEP 0: Pillow and camera.to_pil on a pure blue BGR array")
    try:
        import numpy as np
        import PIL
        _say(f"  Pillow version: {PIL.__version__}")
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        frame[:, :, 0] = 255  # B, G, R in memory: pure blue
        img = camera.to_pil(frame)
        colours = img.convert("RGB").getcolors()
        _say(f"  size {img.size}, mode {img.mode}, colours {colours}")
        if colours == [(16, (0, 0, 255))]:
            _say("  PASS: BGR blue came out as RGB (0, 0, 255)")
            return "PASS"
        _say("  FAIL: expected 16 pixels of RGB (0, 0, 255)")
        return "FAIL"
    except Exception as exc:
        _say(f"  FAIL: {_err(exc)}")
        return "FAIL"


# ---------------------------------------------------------------------------
# Step 1: acquire and release cycles
# ---------------------------------------------------------------------------

def _open_fds():
    try:
        return len(os.listdir(f"{_PROC}/self/fd"))
    except OSError:
        return None


def _proc_kb(path: str, key: str):
    """The integer after `key:` in a /proc file, or None. kB for sizes."""
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key + ":"):
                    return int(line.split(":", 1)[1].split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _sample() -> dict:
    return {
        "fds": _open_fds(),
        "VmRSS": _proc_kb(f"{_PROC}/self/status", "VmRSS"),
        "Threads": _proc_kb(f"{_PROC}/self/status", "Threads"),
        "CmaFree": _proc_kb(f"{_PROC}/meminfo", "CmaFree"),
    }


_UNITS = {"fds": "", "VmRSS": " kB", "Threads": "", "CmaFree": " kB"}


def _fmt(key: str, value) -> str:
    return "n/a" if value is None else f"{value}{_UNITS[key]}"


def _fmt_delta(a, b) -> str:
    if a is None or b is None:
        return "n/a"
    return f"{b - a:+d}"


def step1(cycles: int = _DEFAULT_CYCLES) -> str:
    _say(f"STEP 1: {cycles} acquire and release cycles, no capture")
    _announce_deadline()
    samples = []   # (cycle, sample)
    try:
        for i in range(1, cycles + 1):
            with _held(first=(i == 1)):
                pass
            if i % _SAMPLE_EVERY == 0 or i == cycles:
                s = _sample()
                samples.append((i, s))
                _say(f"  cycle {i}: "
                     + ", ".join(f"{k} {_fmt(k, s[k])}" for k in _UNITS))
    except Refused:
        raise
    except Exception as exc:
        _say(f"  FAIL at cycle {i}: {_err(exc)}")
        return "FAIL"
    if len(samples) < 2:
        _say("  no delta: fewer than two samples")
    else:
        (c0, s0), (c1, s1) = samples[0], samples[-1]
        _say(f"  delta from cycle {c0} to cycle {c1}: "
             + ", ".join(f"{k} {_fmt_delta(s0[k], s1[k])}" for k in _UNITS))
    _say("  No threshold is applied. Read the numbers.")
    _say("  DONE")
    return "DONE"


# ---------------------------------------------------------------------------
# Step 2: the real recovery path
# ---------------------------------------------------------------------------

def step2() -> str:
    _say("STEP 2: forced capture timeout, close, reopen, capture")
    _announce_deadline()
    original = camera._CAPTURE_TIMEOUT_S
    try:
        timed_out = False
        with _held(first=True):
            for attempt in range(1, _FORCED_ATTEMPTS + 1):
                camera._CAPTURE_TIMEOUT_S = _FORCED_TIMEOUT_S
                try:
                    with _deadline():
                        camera.capture_frame()
                except TimeoutError as exc:
                    _say(f"  attempt {attempt}: TimeoutError as expected: "
                         f"{_err(exc)}")
                    timed_out = True
                    break
                else:
                    _say(f"  attempt {attempt}: the {_FORCED_TIMEOUT_S} s "
                         "timeout delivered a frame")
                finally:
                    camera._CAPTURE_TIMEOUT_S = original
        # The release above was the last ref, so it closed the camera.
        if not timed_out:
            _say(f"  INCONCLUSIVE: no TimeoutError in {_FORCED_ATTEMPTS} "
                 "attempts, so the recovery path did not run")
            return "INCONCLUSIVE"
        with _held():
            with _deadline():
                frame = camera.capture_frame()
        shape = tuple(frame.shape)
        _say(f"  PASS: after close and reopen a normal capture gave shape "
             f"{shape}")
        return "PASS"
    except Refused:
        raise
    except Exception as exc:
        _say(f"  FAIL: {_err(exc)}")
        return "FAIL"


# ---------------------------------------------------------------------------
# Step 3: CPU of bender-web during a hold
# ---------------------------------------------------------------------------

def _clk_tck() -> int:
    return os.sysconf("SC_CLK_TCK")


def _cpu_ticks(pid: int) -> int:
    """utime + stime of `pid`, in clock ticks. Raises OSError if it is gone."""
    with open(f"{_PROC}/{pid}/stat") as f:
        data = f.read()
    # The name field can hold spaces and parentheses, so split after the last ")".
    rest = data[data.rindex(")") + 2:].split()
    return int(rest[11]) + int(rest[12])   # fields 14 and 15


def _bender_web_pid():
    """MainPID of bender-web, or None. One read-only systemctl call."""
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", "bender-web"],
            capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT_S)
        pid = int(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return pid if pid > 0 else None


def _touch_warm(queue: str) -> None:
    """Refresh <queue>/warm, as turn_frames.warm() does, with no enabled check."""
    path = os.path.join(queue, "warm")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        os.utime(fd, None)
    finally:
        os.close(fd)


def _camera_links(pid: int) -> int:
    """Open fds of `pid` that point at /dev/video* or /dev/media*.

    The camera shows as these while a process holds it. Raises OSError if the
    process is gone, and PermissionError if its fds belong to another user.
    """
    base = f"{_PROC}/{pid}/fd"
    count = 0
    for name in os.listdir(base):
        try:
            target = os.readlink(f"{base}/{name}")
        except FileNotFoundError:
            continue   # the fd closed between the listing and the read
        if target.startswith(("/dev/video", "/dev/media")):
            count += 1
    return count


def _cpu_percent(pid: int, seconds: float, checks=()):
    """CPU of `pid` as percent of one core over `seconds`.

    `checks` is a sequence of (at_s, fn) in ascending order. Each fn runs once,
    at_s seconds into the window. The window still covers all `seconds`.
    """
    t0, c0 = _now(), _cpu_ticks(pid)
    done = 0.0
    for at, fn in checks:
        _sleep(at - done)
        done = at
        fn()
    _sleep(seconds - done)
    t1, c1 = _now(), _cpu_ticks(pid)
    elapsed = t1 - t0
    if elapsed <= 0:
        return None
    return (c1 - c0) / _clk_tck() / elapsed * 100.0


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _wait_for_release(pid: int) -> int:
    """Poll until bender-web has no camera fd, for at most _RELEASE_WAIT_S.

    Returns the last count. Stops at once when it reaches 0.
    """
    waited = 0.0
    links = _camera_links(pid)
    while links > 0 and waited < _RELEASE_WAIT_S:
        _sleep(_RELEASE_POLL_S)
        waited += _RELEASE_POLL_S
        links = _camera_links(pid)
    return links


def step3() -> str:
    _say("STEP 3: CPU of bender-web during a 15 s camera hold")
    pid = _bender_web_pid()
    if pid is None:
        _say("  SKIP: no running bender-web (systemctl gave no MainPID)")
        return "SKIP"
    queue = os.path.join(_frames_dir(), ".queue")
    if not os.path.isdir(queue):
        _say(f"  SKIP: no queue folder at {queue}. The bender-web worker has "
             "not started.")
        return "SKIP"
    if camera.opened_at() is not None:
        _say("  FAIL: this process holds the camera. Step 3 needs it closed.")
        return "FAIL"
    _say(f"  bender-web PID {pid}")

    taken = {"ok": None}
    seen = []   # camera fd counts, during the window

    def sample():
        seen.append(_camera_links(pid))

    def first_look():
        taken["ok"] = not os.path.exists(os.path.join(queue, "warm"))
        sample()

    checks = [(_WINDOW_SAMPLES_S[0], first_look)]
    checks += [(at, sample) for at in _WINDOW_SAMPLES_S[1:]]

    try:
        before = _camera_links(pid)
        if before > 0:
            _say(f"  INCONCLUSIVE: bender-web already holds the camera "
                 f"({before} fd) before the baseline. See Room may be open, or "
                 "the worker is in a hold. Close See Room, wait 20 s, then run "
                 "step 3 again. Nothing was measured.")
            return "INCONCLUSIVE"
        baseline = _cpu_percent(pid, _BASELINE_S)
        _say(f"  baseline over {_BASELINE_S:.0f} s, before the warm note, "
             f"camera closed: {_pct(baseline)} of one core")
        _touch_warm(queue)
        hold = _cpu_percent(pid, _HOLD_WINDOW_S, checks=checks)
        after = _wait_for_release(pid)
    except PermissionError as exc:
        _say(f"  INCONCLUSIVE: cannot read {_PROC}/{pid}/fd, so a camera hold "
             f"cannot be seen: {_err(exc)}")
        return "INCONCLUSIVE"
    except OSError as exc:
        _say(f"  SKIP: lost bender-web (PID {pid}) or the queue: {_err(exc)}")
        return "SKIP"

    held = any(n > 0 for n in seen)
    if held:
        _say(f"  with the camera held, over {_HOLD_WINDOW_S:.0f} s: "
             f"{_pct(hold)} of one core")
    else:
        _say(f"  over {_HOLD_WINDOW_S:.0f} s, with no camera seen in "
             f"bender-web: {_pct(hold)} of one core. This is not a hold "
             "reading.")
    if baseline is not None and hold is not None:
        _say(f"  delta over the baseline: {hold - baseline:+.1f} points of "
             "one core")
    else:
        _say("  delta over the baseline: n/a")
    _say(f"  camera fds in bender-web: before {before}, in the window "
         f"{seen}, after {after}")
    if not taken["ok"]:
        _say("  INCONCLUSIVE: the warm note was not taken after "
             f"{_NOTE_CHECK_S:.0f} s. The bender-web worker may not be running, "
             "so the reading is not a camera reading.")
        return "INCONCLUSIVE"
    if not held:
        _say("  INCONCLUSIVE: the worker took the note, but no camera link "
             "appeared. It may have failed to open the camera "
             "(see the bender-web journal).")
        return "INCONCLUSIVE"
    if after > 0:
        _say(f"  FAIL: bender-web still holds the camera {_RELEASE_WAIT_S:.0f} "
             "s after the window, which is longer than the 15 s hold. The "
             "worker is not releasing, or See Room was opened.")
        return "FAIL"
    _say("  A hold was seen and it ended. Compare the delta to the baseline.")
    _say("  DONE")
    return "DONE"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

_STEP_NUMBERS = (0, 1, 2, 3)


def _run_step(n: int, cycles: int) -> str:
    """Look the step up by name at call time, so a test can replace one."""
    fn = globals()[f"step{n}"]
    return fn(cycles) if n == 1 else fn()


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number")
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="probe_turn_frames.py",
        description="Device check for turn frames. Run with bender-converse "
                    "running and See Room closed. Never stops a service.")
    p.add_argument("--step", type=int, choices=_STEP_NUMBERS,
                   help="run one step (0 Pillow, 1 cycles, 2 recovery, "
                        "3 bender-web CPU). Default: all, in order.")
    p.add_argument("--cycles", type=_positive_int, default=_DEFAULT_CYCLES,
                   help=f"cycles in step 1 (default {_DEFAULT_CYCLES})")
    p.add_argument("--force", action="store_true",
                   help="run the camera steps even while a conversation "
                        "session is live (.session_active.json exists)")
    return p


def _session_refusal(n: int, force: bool):
    """A one-line refusal for camera step `n`, or None. Step 0 never refuses."""
    if n == 0 or force:
        return None
    if os.path.exists(_session_file()):
        return ("REFUSED: a conversation session is live "
                f"({os.path.basename(_session_file())} exists), and camera "
                "activity near Piper may drop audio. Run again when it ends, "
                "or pass --force.")
    return None


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    steps = [args.step] if args.step is not None else list(_STEP_NUMBERS)
    results = {}
    for n in steps:
        refusal = _session_refusal(n, args.force)
        if refusal is not None:
            _say(refusal)
            return 2
        try:
            results[n] = _run_step(n, args.cycles)
        except Refused as exc:
            _say(f"  {exc.line()}")
            return 2
        except Exception as exc:
            _say(f"  FAIL: step {n} crashed: {_err(exc)}")
            results[n] = "FAIL"
        _say()
    _say("SUMMARY: " + ", ".join(f"step {n} {r}" for n, r in results.items()))
    bad = [n for n, r in results.items() if r in ("FAIL", "INCONCLUSIVE")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
