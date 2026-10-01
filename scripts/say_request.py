"""Ask the running conversation process to speak something, without stopping it.

WHY THIS EXISTS
---------------
Two processes want the one speaker. ``bender-converse`` holds the WM8960 for
the wake loop; ``bender-web`` serves the dashboard. The codec is single-rate,
so they cannot both have it (see CLAUDE.md, "Critical audio constraint").

Until now the web process solved that by stopping bender-converse, taking the
speaker, and starting it again — measured on-device 2026-10-01:

    stop 3.0s | settle 0.5s | play | start 3.0s | wake word listening again 23s

So a soundboard tap was silent for 3.5s, typed text up to 5.9s, and the wake
word was deaf for 23s afterwards while Whisper and Qwen reloaded. Worse, the
unit allows 5 starts per 300s, so the SIXTH puppet action inside five minutes
left Bender dead.

This module replaces that with a note on the floor. The web process writes a
request; the conversation process notices it between wake-word frames and does
the mic-to-speaker handover it already performs for every conversation turn.
No systemctl, no model reload, no start limit.

The pattern is not new here: ``cfg.end_session_file`` and ``cfg.abort_file``
are the same idea, already driven by the web UI.

PROTOCOL
--------
One JSON file, written atomically (temp + ``os.replace``):

    {"id": "...", "wav": "...", "temp": bool, "created": 1234.5, "source": "..."}

* Writing again REPLACES a pending request. That is a deliberate depth-1
  queue: a puppeteer pressing a button twice wants the second thing said, not
  two things queued behind a 5s clip.
* ``claim()`` validates and unlinks, so exactly one consumer plays it.
* A request older than ``max_age_s`` is discarded. A note found 10 minutes
  later must not suddenly speak at a child.

The consumer does NOT trust the file. The path is re-validated on claim: it
must be a ``.wav`` that exists inside the repo. The writer is the web process,
which already validates, but a stale or hand-edited file must not become an
arbitrary-file-read.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from logger import get_logger

log = get_logger("say_request")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The note itself. Gitignored, alongside the other IPC files.
REQUEST_PATH = os.path.join(_BASE_DIR, ".say_request.json")

#: Rendered one-off TTS lands here so the consumer can delete it after playing.
#: The web process cannot delete it on return any more -- it is no longer the
#: one doing the playing.
TEMP_DIR = os.path.join(_BASE_DIR, ".say_tmp")

#: Default staleness limit. Long enough to survive a conversation turn in
#: progress (the measured turn_total tail is 19s), short enough that a request
#: never outlives the moment the operator meant it for.
DEFAULT_MAX_AGE_S = 45.0

#: Orphan sweep for TEMP_DIR: a request that is never claimed leaves its WAV.
TEMP_ORPHAN_AGE_S = 300.0


def _safe_wav(path: str) -> str | None:
    """Return an absolute path inside the repo, or None. Never trusts input."""
    if not path or not isinstance(path, str) or not path.endswith(".wav"):
        return None
    resolved = os.path.realpath(
        path if os.path.isabs(path) else os.path.join(_BASE_DIR, path))
    base = os.path.realpath(_BASE_DIR)
    if not (resolved == base or resolved.startswith(base + os.sep)):
        return None
    if not os.path.isfile(resolved):
        return None
    return resolved


def temp_dir() -> str:
    os.makedirs(TEMP_DIR, exist_ok=True)
    return TEMP_DIR


def request(wav_path: str, *, temp: bool = False, source: str = "web") -> str | None:
    """Ask the conversation process to play ``wav_path``. Returns the id.

    Replaces any pending request (depth-1 queue). Returns None if the path is
    not a playable WAV inside the repo.
    """
    resolved = _safe_wav(wav_path)
    if resolved is None:
        log.warning("say_request refused a bad path: %r", wav_path)
        return None
    req_id = uuid.uuid4().hex[:12]
    payload = {
        "id": req_id,
        "wav": resolved,
        "temp": bool(temp),
        "created": time.time(),
        "source": source,
    }
    tmp = REQUEST_PATH + ".part"
    try:
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, REQUEST_PATH)        # atomic: no half-written note
    except OSError as exc:
        log.error("say_request could not write the request: %s", exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return req_id


def pending() -> bool:
    """Cheap enough to call on every 80ms wake-word frame."""
    return os.path.exists(REQUEST_PATH)


def claim(max_age_s: float = DEFAULT_MAX_AGE_S) -> dict | None:
    """Take the pending request, if there is a valid one.

    Unlinks first, so a malformed or stale note cannot be claimed twice and
    cannot wedge the wake loop into retrying it for ever.
    """
    try:
        with open(REQUEST_PATH) as f:
            raw = f.read()
    except OSError:
        return None
    finally:
        try:
            os.unlink(REQUEST_PATH)
        except OSError:
            pass

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("say_request discarded a malformed request")
        return None
    if not isinstance(payload, dict):
        return None

    age = time.time() - float(payload.get("created", 0) or 0)
    if age > max_age_s:
        log.warning("say_request discarded a stale request (%.0fs old)", age)
        _discard_temp(payload)
        return None

    resolved = _safe_wav(payload.get("wav", ""))
    if resolved is None:
        log.warning("say_request discarded an unplayable path: %r",
                    payload.get("wav"))
        return None
    payload["wav"] = resolved
    return payload


def done(payload: dict) -> None:
    """Call after playing. Deletes the WAV if it was a one-off render."""
    _discard_temp(payload)


def _discard_temp(payload: dict) -> None:
    if not payload.get("temp"):
        return
    path = _safe_wav(payload.get("wav", ""))
    if path is None:
        return
    # Only ever inside TEMP_DIR: a `temp: true` flag on a real clip must not
    # delete the clip library.
    if not path.startswith(os.path.realpath(TEMP_DIR) + os.sep):
        log.warning("say_request refused to delete a non-temp WAV: %s", path)
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def clear() -> None:
    for p in (REQUEST_PATH, REQUEST_PATH + ".part"):
        try:
            os.unlink(p)
        except OSError:
            pass


def sweep_temp(older_than_s: float = TEMP_ORPHAN_AGE_S) -> int:
    """Delete orphaned one-off renders. Returns how many went.

    A request that is never claimed (converse down, or the operator stopped it)
    leaves its WAV behind, and these are a few hundred KB each.
    """
    removed = 0
    now = time.time()
    try:
        names = os.listdir(TEMP_DIR)
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".wav"):
            continue
        path = os.path.join(TEMP_DIR, name)
        try:
            if now - os.path.getmtime(path) > older_than_s:
                os.unlink(path)
                removed += 1
        except OSError:
            pass
    return removed
