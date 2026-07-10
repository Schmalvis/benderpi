"""Process-wide guard for the bender-converse stop/play/restart dance.

The web UI has several actions that must briefly take exclusive control of the
audio hardware away from the live wake loop: puppet speak/clip playback, the
vision auto-narrate, and the ambient mic stream. Each one does the same thing —
check whether ``bender-converse`` is active, stop it, do its work, then restart
it. Run two of these concurrently and they race: both read "active", both stop,
and the first restart brings the wake loop back up *on top of* the second clip
still playing on the single-rate WM8960.

This module serialises that whole sequence behind one process-wide
``threading.Lock``. It is deliberately a *sync* context manager (threading, not
asyncio) so it works identically from:

  * async route handlers — enter it via ``asyncio.to_thread(...)`` / the
    ``service_lease_async`` helper, and
  * plain worker threads — FastAPI ``BackgroundTasks`` (vision.py) run outside
    the event loop, so an ``asyncio.Lock`` would be the wrong tool.

Acquire is bounded: if another lease is already held, we wait up to
``acquire_timeout`` seconds and then raise :class:`ServiceBusy` so the caller
can surface a 409 rather than queueing indefinitely behind a long TTS clip.

The systemctl stop/restart itself lives here too (``_stop_converse`` /
``_start_converse``) so the duplicated logic in puppet.py and vision.py can be
deleted — there is now exactly one place that touches the service lifecycle.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time

from logger import get_logger

log = get_logger("service_guard")
audit = get_logger("audit")

_IS_LINUX = os.name != "nt"

# One lock for the whole process. Held for the full stop -> work -> restart
# sequence so overlapping web actions can never interleave their systemctl
# calls or their audio playback.
_lock = threading.Lock()

# Default seconds to wait for the lock before giving up with ServiceBusy. Kept
# short: a second puppet request should fail fast, not silently queue behind a
# 15s clip. Tunable per-call.
DEFAULT_ACQUIRE_TIMEOUT = 2.0


class ServiceBusy(RuntimeError):
    """Raised when the service guard is already held and the wait timed out."""


def _is_converse_active() -> bool:
    if not _IS_LINUX:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "bender-converse"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except Exception:
        log.warning("service_guard: could not query bender-converse state", exc_info=True)
        return False


def _stop_converse() -> None:
    if not _IS_LINUX:
        return
    subprocess.run(
        ["sudo", "systemctl", "stop", "bender-converse"],
        capture_output=True, text=True, timeout=15,
    )


def _start_converse() -> None:
    if not _IS_LINUX:
        return
    subprocess.run(
        ["sudo", "systemctl", "start", "bender-converse"],
        capture_output=True, text=True, timeout=15,
    )


@contextlib.contextmanager
def guard_lock(acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT):
    """Serialise against :func:`service_lease` without its stop-before /
    restart-after dance.

    Use this when the caller performs its own explicit systemctl action
    (restart / stop / start) that must not race puppet playback, the vision
    auto-narrate, or the mic websocket for control of ``bender-converse`` --
    e.g. the web UI's manual restart/toggle-mode buttons. Unlike
    ``service_lease``, nothing is stopped or restarted on entry/exit; the
    caller owns that.

    Raises
    ------
    ServiceBusy
        If the lock could not be acquired within ``acquire_timeout``.
    """
    acquired = _lock.acquire(timeout=acquire_timeout)
    if not acquired:
        raise ServiceBusy("bender-converse audio guard is busy")
    try:
        yield
    finally:
        _lock.release()


@contextlib.contextmanager
def service_lease(acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT,
                  stop_converse: bool = True):
    """Serialise exclusive use of the audio hardware across web actions.

    Acquires the process-wide lock (waiting at most ``acquire_timeout`` s), then
    — if ``stop_converse`` and the service is running — stops bender-converse so
    the caller has sole access to the WM8960. On exit the service is restarted
    iff this lease stopped it. Always releases the lock, even on error.

    Parameters
    ----------
    acquire_timeout:
        Max seconds to wait for the lock. On timeout raises :class:`ServiceBusy`.
    stop_converse:
        When False, still serialise against other leases but do not touch the
        service (e.g. a Windows dev box, or a caller that does not contend for
        the capture device). Nothing is restarted on exit in that case.

    Raises
    ------
    ServiceBusy
        If the lock could not be acquired within ``acquire_timeout``.
    """
    acquired = _lock.acquire(timeout=acquire_timeout)
    if not acquired:
        raise ServiceBusy("bender-converse audio guard is busy")
    was_running = False
    try:
        if stop_converse and _is_converse_active():
            was_running = True
            _stop_converse()
            # Let the wake loop fully release the capture/playback device before
            # the caller opens it — the WM8960 is single-rate and the mic array
            # read must have drained.
            time.sleep(0.5)
        yield
    finally:
        try:
            if was_running:
                _start_converse()
        finally:
            _lock.release()
