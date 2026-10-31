"""Turn frames: a camera frame each time Bender starts to reply.

Two processes share this module.

bender-converse writes small notes into <frames>/.queue and never opens the
camera:

    request(name)   one empty file, named "<YYYY-MM-DD>__<name>"
    warm()          one fixed file, "warm", that asks for the camera to be
                    opened early and takes no frame

bender-web runs a daemon thread, the worker, that owns the camera:

    start_worker()  idempotent, never raises
    stop_worker()   sets the stop event and joins for at most 1 s. It never
                    waits on the camera
    status()        reads the disk and the thread only. Never touches camera

Each worker pass lists the queue and removes EVERY note before any camera
call. A crash then cannot retry a note for ever. The newest fresh frame note
is captured. Older fresh ones record "dropped". A note over _STALE_S old, by
abs(now - mtime) because the clock steps at boot, records "stale".

Every frame note gets one line in <frames>/<date>/outcomes.jsonl:
saved, failed, stale or dropped. A warm note gets no line.

Settings
--------
enabled() reads the live `cfg`, which is right for bender-converse.
bender-web does not restart on a config save, so its `cfg` is stale. The
worker and status() read their settings from bender_config.json at use, through
_setting(), and fall back to `cfg`, then to the default.

Failure rule: nothing here may stop Bender talking. request(), warm(),
start_worker(), stop_worker() and status() never raise.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
import shutil
import stat
import threading
import time

import camera
import config
from camera import CameraBusy
from config import cfg
from logger import get_logger

log = get_logger("turn_frames")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Module constants, patchable by tests.
_POLL_S = 0.2
_STALE_S = 5.0
_HOLD_S = 15.0
_MAX_WAITING = 32
_SWEEP_EVERY_S = 3600.0
_SIZE = (1280, 720)
_JPEG_QUALITY = 80
_DEFAULT_RETENTION_DAYS = 7
_DEFAULT_SETTLE_S = 0.5
_LOG_EVERY_S = 60.0

_WARM = "warm"
_QUEUE = ".queue"
_NAME_RE = re.compile(r"[\w.-]{1,64}", re.ASCII)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)

_current = None  # the latest _Worker, for status() and stop_worker()
_current_lock = threading.Lock()
_last_logged: dict = {}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _throttled(key: str) -> bool:
    """True at most once per _LOG_EVERY_S for each key."""
    now = time.monotonic()
    last = _last_logged.get(key)
    if last is not None and now - last < _LOG_EVERY_S:
        return False
    _last_logged[key] = now
    return True


def _warn(key: str, msg: str, *args) -> None:
    """Log a warning at most once a minute per key. Never raises."""
    try:
        if _throttled(key):
            log.warning(msg, *args)
    except Exception:
        pass


def _err(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:200]


def _number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def frames_dir() -> str:
    """<log dir>/frames. Read at call time. Writer, sweep and status share it."""
    base = os.environ.get("BENDER_LOG_DIR") or os.path.join(_BASE_DIR, "logs")
    return os.path.join(base, "frames")


def enabled() -> bool:
    """True only when `turn_frames_enabled` is literally True. Never raises."""
    try:
        return getattr(cfg, "turn_frames_enabled", False) is True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Settings that bender-web must read fresh
# ---------------------------------------------------------------------------

def _setting(key, default, valid):
    """Value of `key` from bender_config.json on disk, else cfg, else default.

    A key that is in the file but invalid gives the default. That includes a
    hand-edited retention under 1, which bypasses the schema. A missing or
    corrupt file never raises.
    """
    try:
        with open(config._DEFAULT_CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict) and key in data:
            value = data[key]
            return value if valid(value) else default
    except Exception:
        pass
    try:
        value = getattr(cfg, key, None)
    except Exception:
        return default
    return value if valid(value) else default


def _retention_days():
    return _setting("turn_frames_retention_days", _DEFAULT_RETENTION_DAYS,
                    lambda v: _number(v) and v >= 1)


def _settle_s() -> float:
    value = _setting("turn_frames_settle_s", _DEFAULT_SETTLE_S,
                     lambda v: _number(v) and 0 <= v <= 5)
    return float(value)


def _enabled_saved() -> bool:
    try:
        with open(config._DEFAULT_CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict) and "turn_frames_enabled" in data:
            return data["turn_frames_enabled"] is True
    except Exception:
        pass
    return enabled()


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def _mkdir_private(path: str) -> None:
    """Create one level at 0700. Two processes race, so FileExistsError is fine."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    os.chmod(path, 0o700)


def _ensure_frames() -> str:
    root = frames_dir()
    os.makedirs(os.path.dirname(root), exist_ok=True)  # mode left alone
    _mkdir_private(root)
    return root


def _ensure_queue() -> str:
    q = os.path.join(_ensure_frames(), _QUEUE)
    _mkdir_private(q)
    return q


def _ensure_day(day: str) -> str:
    d = os.path.join(_ensure_frames(), day)
    _mkdir_private(d)
    return d


# ---------------------------------------------------------------------------
# Notes: the bender-converse side
# ---------------------------------------------------------------------------

def request(name: str):
    """Leave one note asking for a frame. Returns "<date>/<name>.jpg" or None."""
    try:
        if not enabled():
            return None
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            return None
        q = _ensure_queue()
        if len(os.listdir(q)) >= _MAX_WAITING:
            _warn("backlog", "turn frame skipped: %d notes already wait",
                  _MAX_WAITING)
            return None
        day = datetime.datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(q, f"{day}__{name}")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600)
        os.close(fd)
        return f"{day}/{name}.jpg"
    except FileExistsError:
        return None
    except Exception as exc:
        _warn("request", "turn frame request failed: %s", _err(exc))
        return None


def warm() -> None:
    """Ask the worker to open the camera early. No frame. Never raises."""
    try:
        if not enabled():
            return
        path = os.path.join(_ensure_queue(), _WARM)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            os.utime(fd, None)
        finally:
            os.close(fd)
    except Exception as exc:
        _warn("warm", "turn frame warm failed: %s", _err(exc))


def _parse_note(fname: str):
    """(date, name) for a frame note, or None. Validates before any path."""
    day, sep, name = fname.partition("__")
    if not sep or not _DATE_RE.fullmatch(day) or not _NAME_RE.fullmatch(name):
        return None
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        return None
    return day, name


# ---------------------------------------------------------------------------
# Saving and outcomes
# ---------------------------------------------------------------------------

def _encode_and_save(img, day: str, name: str) -> str:
    """Resize, encode and write <frames>/<day>/<name>.jpg atomically."""
    from PIL import Image  # lazy: importing this module must not pull PIL

    img = img.resize(_SIZE, Image.Resampling.BILINEAR)
    final = os.path.join(_ensure_day(day), name + ".jpg")
    tmp = final + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            fd = None
            img.save(f, format="JPEG", quality=_JPEG_QUALITY)
        os.replace(tmp, final)
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return final


def _iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat()


def _record(day: str, name: str, outcome: str, error, mtime_ns: int,
            taken_at) -> None:
    """Append one outcome line. A failure here is logged and never raised."""
    try:
        row = {
            "name": name,
            "outcome": outcome,
            "error": error,
            "requested_at": _iso(mtime_ns / 1e9),
            "taken_at": taken_at,
        }
        data = (json.dumps(row) + "\n").encode()
        path = os.path.join(_ensure_day(day), "outcomes.jsonl")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, data)
        finally:
            os.close(fd)
    except Exception as exc:
        _warn("outcome", "turn frame outcome not written: %s", _err(exc))


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def _sweep() -> None:
    """Remove expired dated folders. Never raises. Runs with the feature off."""
    try:
        retention = _retention_days()
        today = datetime.date.today()
        root = frames_dir()
        with os.scandir(root) as it:
            entries = list(it)
        for entry in entries:
            try:
                if not _DATE_RE.fullmatch(entry.name):
                    continue
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    folder_date = datetime.date.fromisoformat(entry.name)
                except ValueError:
                    continue
                if (today - folder_date).days >= retention:
                    shutil.rmtree(entry.path)
            except Exception as exc:
                _warn("sweep_one", "turn frame sweep skipped %s: %s",
                      entry.name, _err(exc))
    except FileNotFoundError:
        pass
    except Exception as exc:
        _warn("sweep", "turn frame sweep failed: %s", _err(exc))


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

class _Worker:
    """State and logic of one worker thread. `run_pass` needs no thread."""

    def __init__(self):
        self.stop = threading.Event()
        self.thread = None
        self.held = False          # worker-local: this worker holds a camera ref
        self.hold_until = 0.0      # monotonic
        self.last_sweep = None     # monotonic
        self.last_pass = time.monotonic()
        self.startup_done = False

    # -- loop ---------------------------------------------------------------

    def _main(self, stop) -> None:
        try:
            while not stop.is_set():
                try:
                    self.run_pass(stop, startup=not self.startup_done)
                except Exception as exc:
                    _warn("loop", "turn frames worker pass failed: %s",
                          _err(exc))
                stop.wait(_POLL_S)
        finally:
            if self.held:
                self._release()

    # -- one pass -----------------------------------------------------------

    def run_pass(self, stop, startup: bool = False) -> None:
        # last_pass means "when the worker last BEGAN a pass". It is set first,
        # before anything that can raise, so a filesystem fault does not read
        # as a stuck worker. A worker stuck in a C++ call never begins the
        # next pass, and that is what worker_stuck_s shows.
        self.last_pass = time.monotonic()
        try:
            self._run_pass_body(stop, startup)
        finally:
            # Whatever the pass did, an expired hold must release the camera.
            # _expire_hold never raises, so it cannot mask the pass's error.
            self._expire_hold()

    def _expire_hold(self) -> None:
        try:
            if self.held and time.monotonic() >= self.hold_until:
                self._release()
        except Exception as exc:
            _warn("expire", "turn frames: hold check failed: %s", _err(exc))

    def _run_pass_body(self, stop, startup: bool) -> None:
        q = _ensure_queue()
        if (self.last_sweep is None
                or self.last_pass - self.last_sweep >= _SWEEP_EVERY_S):
            self.last_sweep = self.last_pass  # first, so a raise cannot spin
            _sweep()

        notes = self._take_notes(q)
        if startup:
            self.startup_done = True

        now = time.time()
        fresh = []         # (mtime_ns, fname, day, name)
        warm_fresh = False
        saw_note = False
        for fname, mtime_ns in notes:
            stale = startup or abs(now - mtime_ns / 1e9) > _STALE_S
            if fname == _WARM:
                if not stale:
                    warm_fresh = True
                    saw_note = True
                continue
            parsed = _parse_note(fname)
            if parsed is None:
                _warn("badnote", "turn frames: removed an unparseable note %r",
                      fname)
                continue
            saw_note = True
            day, name = parsed
            if stale:
                _record(day, name, "stale", None, mtime_ns, None)
            else:
                fresh.append((mtime_ns, fname, day, name))

        fresh.sort()
        newest = fresh.pop() if fresh else None
        for mtime_ns, _fname, day, name in fresh:
            _record(day, name, "dropped", None, mtime_ns, None)

        capture_failed = False
        if newest is not None or warm_fresh:
            if not self.held:
                try:
                    camera.acquire_camera()
                    self.held = True
                except Exception as exc:
                    _warn("acquire", "turn frames: camera not available: %s",
                          _err(exc))
                    if newest is not None:
                        mtime_ns, _f, day, name = newest
                        _record(day, name, "failed", _err(exc), mtime_ns, None)
                        newest = None
            if self.held and newest is not None:
                capture_failed = self._take(newest, stop)

        if self.held:
            if capture_failed:
                self.hold_until = 0.0  # a sick camera is not held for 15 s
            elif saw_note:
                self.hold_until = time.monotonic() + _HOLD_S

    def _take_notes(self, q: str):
        """List the queue and remove every note. Returns [(name, mtime_ns)]."""
        listed = []
        with os.scandir(q) as it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue  # vanished
                if stat.S_ISDIR(st.st_mode):
                    continue
                listed.append((entry.name, st.st_mtime_ns, entry.path))
        taken = []
        for fname, mtime_ns, path in listed:
            try:
                os.unlink(path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                _warn("unlink", "turn frames: cannot remove note %r: %s",
                      fname, _err(exc))
                continue
            taken.append((fname, mtime_ns))
        return taken

    def _take(self, newest, stop) -> bool:
        """Settle, capture, encode, save one frame. True if the CAPTURE failed."""
        mtime_ns, _fname, day, name = newest
        opened = camera.opened_at()
        if opened is not None:
            remaining = opened + _settle_s() - time.monotonic()
            if remaining > 0 and stop.wait(remaining):
                _record(day, name, "failed", "Stopped: worker is stopping",
                        mtime_ns, None)
                return False
        try:
            frame = camera.capture_frame()
        except Exception as exc:
            _warn("capture", "turn frames: capture failed: %s", _err(exc))
            _record(day, name, "failed", _err(exc), mtime_ns, None)
            return True
        taken_at = _iso(time.time())
        try:
            img = camera.to_pil(frame)
            _encode_and_save(img, day, name)
        except Exception as exc:
            _warn("save", "turn frames: frame not saved: %s", _err(exc))
            _record(day, name, "failed", _err(exc), mtime_ns, taken_at)
            return False
        _record(day, name, "saved", None, mtime_ns, taken_at)
        return False

    def _release(self) -> None:
        """Release the camera ref. A CameraBusy keeps it, for a later pass."""
        try:
            camera.release_camera()
            self.held = False
        except CameraBusy as exc:
            _warn("release", "turn frames: camera release will retry: %s",
                  _err(exc))
        except Exception as exc:
            _warn("release_other", "turn frames: camera release failed: %s",
                  _err(exc))
            self.held = False


# ---------------------------------------------------------------------------
# start, stop, status
# ---------------------------------------------------------------------------

def start_worker() -> None:
    """Start the worker thread. A live worker means no second thread."""
    global _current
    try:
        with _current_lock:
            if (_current is not None and _current.thread is not None
                    and _current.thread.is_alive()):
                return
            worker = _Worker()
            worker.thread = threading.Thread(
                target=worker._main, args=(worker.stop,),
                name="turn-frames-worker", daemon=True)
            worker.thread.start()
            _current = worker
    except Exception as exc:
        _warn("start", "turn frames worker did not start: %s", _err(exc))


def stop_worker() -> None:
    """Stop the worker. Joins for at most 1 s. Never waits on the camera."""
    try:
        with _current_lock:
            worker = _current
        if worker is None:
            return
        worker.stop.set()
        thread = worker.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
    except Exception as exc:
        _warn("stop", "turn frames worker stop failed: %s", _err(exc))


def _read_outcomes(day: str):
    path = os.path.join(frames_dir(), day, "outcomes.jsonl")
    rows = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def status() -> dict:
    """Disk and thread state only. It never touches the camera. Never raises."""
    out = {
        "enabled_saved": False,
        "saved_today": 0,
        "failed_today": 0,
        "stale_today": 0,
        "dropped_today": 0,
        "last_saved": None,
        "waiting_notes": 0,
        "oldest_note_age_s": None,
        "worker_alive": False,
        "worker_stuck_s": None,
    }
    try:
        out["enabled_saved"] = _enabled_saved()
    except Exception:
        pass
    try:
        day = datetime.date.today().isoformat()
        for row in _read_outcomes(day):
            outcome = row.get("outcome")
            if outcome == "saved":
                out["saved_today"] += 1
                out["last_saved"] = row.get("taken_at")
            elif outcome in ("failed", "stale", "dropped"):
                out[outcome + "_today"] += 1
    except Exception:
        pass
    try:
        now = time.time()
        oldest = None
        count = 0
        with os.scandir(os.path.join(frames_dir(), _QUEUE)) as it:
            for entry in it:
                count += 1
                try:
                    age = abs(now - entry.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    continue
                oldest = age if oldest is None else max(oldest, age)
        out["waiting_notes"] = count
        out["oldest_note_age_s"] = None if oldest is None else round(oldest, 1)
    except Exception:
        pass
    try:
        worker = _current
        if worker is not None:
            thread = worker.thread
            out["worker_alive"] = bool(thread is not None and thread.is_alive())
            out["worker_stuck_s"] = round(time.monotonic() - worker.last_pass, 1)
    except Exception:
        pass
    return out
