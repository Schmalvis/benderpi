import asyncio
import json
import os
import sys
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from web.auth import require_stream_token_ws, require_token, verify_stream_token
from web.service_guard import ServiceBusy, converse_is_running, service_lease

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

import audio
import leds
import say_request
import vision as _vision
from config import cfg
from logger import get_logger

log = get_logger("puppet")
_IS_LINUX = os.name != "nt"
_audit = get_logger("audit")


def _client_ip(request: Request | None) -> str:
    if request is None or request.client is None:
        return "?"
    return request.client.host
_WAV_DIR = os.path.join(_BASE_DIR, "speech", "wav")
_INDEX_PATH = os.path.join(_BASE_DIR, "speech", "responses", "index.json")
_FAVOURITES_PATH = os.path.join(_BASE_DIR, "favourites.json")
_CLIP_CATEGORIES_PATH = os.path.join(_BASE_DIR, "speech", "clip_categories.json")
_CLIP_LABELS_PATH = os.path.join(_BASE_DIR, "speech", "clip_labels.json")

_camera_available_cache: tuple | None = None
_CAMERA_CACHE_TTL = 10.0

router = APIRouter()


# ---------------------------------------------------------------------------
# Clip helpers
# ---------------------------------------------------------------------------

def _load_favourites() -> list[str]:
    try:
        with open(_FAVOURITES_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return []


def _save_favourites(favs: list[str]) -> None:
    with open(_FAVOURITES_PATH, "w") as f:
        json.dump(favs, f, indent=2)


def _normalise_entry(entry):
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        return entry.get("file", ""), entry.get("label")
    return str(entry), None


def _load_clip_categories() -> dict[str, str]:
    try:
        with open(_CLIP_CATEGORIES_PATH, "r") as f:
            data = json.load(f)
        return {fname: cat for cat, fnames in data.items() for fname in fnames}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_clip_labels() -> dict[str, str]:
    try:
        with open(_CLIP_LABELS_PATH, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _get_clips() -> list[dict]:
    favs = set(_load_favourites())
    clip_cats = _load_clip_categories()
    clip_labels = _load_clip_labels()
    clips = {}

    if os.path.isdir(_WAV_DIR):
        for fname in sorted(os.listdir(_WAV_DIR)):
            if fname.lower().endswith(".wav"):
                rel = "speech/wav/" + fname
                name = os.path.splitext(fname)[0]
                clips[rel] = {
                    "path": rel, "name": name,
                    "label": clip_labels.get(fname, name),
                    "category": clip_cats.get(fname, "clips"),
                    "favourite": rel in favs,
                }

    try:
        with open(_INDEX_PATH, "r") as f:
            index = json.load(f)
    except (OSError, json.JSONDecodeError):
        index = {}

    for category, entries in index.items():
        if category == "promoted":
            if isinstance(entries, list):
                for entry in entries:
                    file_path, label = _normalise_entry(entry)
                    if file_path and file_path not in clips:
                        name = os.path.splitext(os.path.basename(file_path))[0]
                        clips[file_path] = {
                            "path": file_path, "name": name,
                            "label": label or name, "category": "promoted",
                            "favourite": file_path in favs,
                        }
        elif isinstance(entries, list):
            for entry in entries:
                file_path, label = _normalise_entry(entry)
                if file_path and file_path not in clips:
                    name = os.path.splitext(os.path.basename(file_path))[0]
                    clips[file_path] = {
                        "path": file_path, "name": name,
                        "label": label or name, "category": category,
                        "favourite": file_path in favs,
                    }
        elif isinstance(entries, dict):
            for sub_key, entry in entries.items():
                file_path, label = _normalise_entry(entry)
                if file_path and file_path not in clips:
                    clips[file_path] = {
                        "path": file_path, "name": sub_key,
                        "label": label or sub_key, "category": category,
                        "favourite": file_path in favs,
                    }

    return list(clips.values())


# ---------------------------------------------------------------------------
# Audio helper
# ---------------------------------------------------------------------------

def _move_to_say_temp(wav_path: str) -> str:
    """Move a freshly rendered WAV into the say-request temp directory.

    tts_generate.speak() returns a path in the system temp dir, which the
    conversation process would refuse to play (say_request only accepts paths
    inside the repo, so a stale note can never be an arbitrary-file read).
    """
    import shutil
    dest_dir = say_request.temp_dir()
    dest = os.path.join(dest_dir, f"say_{int(time.time() * 1000)}.wav")
    try:
        shutil.move(wav_path, dest)
        return dest
    except OSError as exc:
        log.warning("could not move the render into %s (%s) — playing in place",
                    dest_dir, exc)
        return wav_path


def _play_guarded(wav_path: str) -> None:
    """Blocking: acquire the service guard (stopping bender-converse), play the
    WAV, then restart. Runs in a worker thread — the guard is a sync lock held
    for the whole stop/play/restart sequence so concurrent web actions serialise
    instead of racing on the single-rate WM8960. Raises ServiceBusy if the guard
    is already held (a play is in progress)."""
    with service_lease():
        leds.set_talking()
        audio.play_oneshot(wav_path, leds.set_level, leds.all_off)


async def _puppet_play(wav_path: str, *, temp: bool = False) -> dict:
    """Play a WAV on Bender, by the fastest route available.

    TWO ROUTES, and the choice matters:

    1. bender-converse is RUNNING -> hand it a "say this" request and let it
       play through the speaker it already owns. No systemctl, no model
       reload. The old route stopped the service and restarted it, which
       measured (on-device 2026-10-01) 3.5s of silence before a soundboard
       clip, 23s of deafness afterwards, and a start-limit kill on the sixth
       action in five minutes.

    2. bender-converse is NOT running (puppet-only mode, or it crashed) ->
       nobody owns the speaker, so take it directly behind the service guard,
       exactly as before. This is why the guard stays.

    Returns what happened, so the UI can tell the operator which route ran.
    """
    if await asyncio.to_thread(converse_is_running):
        req_id = await asyncio.to_thread(
            lambda: say_request.request(wav_path, temp=temp, source="puppet"))
        if req_id:
            return {"route": "ipc", "id": req_id}
        # Fall through: a refused request means a bad path, and the direct
        # route validates again rather than silently doing nothing.
        log.warning("say_request refused %s — falling back to the direct route",
                    wav_path)
    try:
        await asyncio.to_thread(_play_guarded, wav_path)
        return {"route": "direct"}
    except ServiceBusy:
        raise HTTPException(status_code=409, detail="Bender is already speaking — try again in a moment")


# ---------------------------------------------------------------------------
# Camera helper
# ---------------------------------------------------------------------------

def _check_camera() -> bool:
    global _camera_available_cache
    now = time.monotonic()
    if _camera_available_cache is not None:
        cached_result, cached_ts = _camera_available_cache
        if now - cached_ts < _CAMERA_CACHE_TTL:
            return cached_result
    try:
        _vision.acquire_camera()
    except Exception:
        _camera_available_cache = (False, now)
        return False
    try:
        _camera_available_cache = (True, now)
        return True
    finally:
        _vision.release_camera()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/puppet/clips", dependencies=[Depends(require_token)])
async def puppet_clips():
    return {"clips": _get_clips()}


@router.post("/api/puppet/speak", dependencies=[Depends(require_token)])
async def puppet_speak(request: Request, body: dict = Body(...)):
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    if len(text) > 500:
        raise HTTPException(status_code=400, detail="Text too long (max 500 chars)")
    _audit.info("puppet.speak from %s (%d chars)", _client_ip(request), len(text))
    import tts_generate
    wav_path = await asyncio.to_thread(tts_generate.speak, text)
    # The render has to outlive this request now: on the IPC route the
    # conversation process plays it later and deletes it afterwards
    # (say_request.done). Deleting it here would hand Bender a missing file.
    wav_path = await asyncio.to_thread(_move_to_say_temp, wav_path)
    await asyncio.to_thread(say_request.sweep_temp)   # orphans from past requests
    try:
        result = await _puppet_play(wav_path, temp=True)
    except Exception:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        raise
    if result.get("route") == "direct":
        # The direct route played it inline, so it is finished with it.
        try:
            os.unlink(wav_path)
        except OSError:
            pass
    return {"status": "ok", "text": text, **result}


@router.post("/api/puppet/clip", dependencies=[Depends(require_token)])
async def puppet_clip(request: Request, body: dict = Body(...)):
    path = body.get("path", "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="No path provided")
    if not path.endswith(".wav"):
        raise HTTPException(status_code=400, detail="Path must end in .wav")
    resolved = os.path.normpath(os.path.join(_BASE_DIR, path))
    if not resolved.startswith(os.path.normpath(_BASE_DIR)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="Clip not found")
    _audit.info("puppet.clip %s from %s", path, _client_ip(request))
    result = await _puppet_play(resolved)
    return {"status": "ok", "path": path, **result}


@router.post("/api/puppet/favourite", dependencies=[Depends(require_token)])
async def puppet_favourite(body: dict = Body(...)):
    path = body.get("path", "").strip()
    favourite = body.get("favourite", True)
    if not path:
        raise HTTPException(status_code=400, detail="No path provided")
    favs = _load_favourites()
    if favourite and path not in favs:
        favs.append(path)
    elif not favourite and path in favs:
        favs.remove(path)
    _save_favourites(favs)
    return {"status": "ok", "path": path, "favourite": favourite}


@router.get("/api/puppet/camera/status", dependencies=[Depends(require_token)])
async def puppet_camera_status():
    available = await asyncio.to_thread(_check_camera)
    return {"available": available}


@router.get("/api/puppet/camera/stream")
async def puppet_camera_stream(token: str = ""):
    """MJPEG stream. A short-lived stream token is passed as a query param
    (browser <img src> can't set headers). Validated only here, at connection
    open — a token expiring mid-stream must never kill a live camera feed."""
    if not verify_stream_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired stream token")
    available = await asyncio.to_thread(_check_camera)
    if not available:
        raise HTTPException(status_code=503, detail="Camera not available")

    max_s = float(getattr(cfg, "web_stream_max_s", 300.0))

    async def _generate():
        import io
        from PIL import Image
        cam = await asyncio.to_thread(_vision.acquire_camera)
        deadline = time.monotonic() + max_s if max_s > 0 else None
        try:
            while True:
                # Wall-clock cap: a backgrounded mobile tab that stops reading
                # (but never cleanly closes) would otherwise pin the camera
                # forever — the exact resource-silently-held failure shape.
                if deadline is not None and time.monotonic() >= deadline:
                    break
                frame = await asyncio.to_thread(cam.capture_array)
                buf = io.BytesIO()
                Image.fromarray(frame).save(buf, format="JPEG", quality=70)
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.getvalue() + b"\r\n"
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            await asyncio.to_thread(_vision.release_camera)

    return StreamingResponse(_generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.websocket("/ws/puppet/mic")
async def puppet_mic_ws(websocket: WebSocket):
    """Stream ambient mic audio to operator (PCM 16 kHz mono S16_LE).

    Auth is a short-lived stream token in the ``token`` query param, validated
    once at connection open (never per-frame).

    ``arecord -D default`` opens the same capture device the live wake loop is
    reading, so this holds the process-wide service guard for the whole stream
    (stopping bender-converse) — otherwise two readers contend for the single
    device. The stream is bounded by a wall-clock cap and a per-send timeout so
    a backgrounded tab that stops reading gets cut off instead of pinning
    ``arecord`` (and the mic) indefinitely."""
    if not await require_stream_token_ws(websocket):
        return

    # Acquire the guard before accepting — if a puppet clip is playing, fail the
    # handshake fast rather than fighting over the device. Hold the same context
    # manager object so we can release it (restart bender-converse) on close.
    _lease_cm = service_lease()
    try:
        await asyncio.to_thread(_lease_cm.__enter__)
    except ServiceBusy:
        await websocket.close(code=4009)
        return

    CHUNK = 4096
    max_s = float(getattr(cfg, "web_mic_max_s", 120.0))
    proc = None
    try:
        # accept() lives inside this try/finally too — if it raises (e.g. the
        # client disconnected between the token check and here), the lease
        # must still be released in the finally below rather than stranded.
        await websocket.accept()
        _audit.info("puppet.mic_stream opened from %s", _client_ip(websocket))
        deadline = time.monotonic() + max_s if max_s > 0 else None
        proc = await asyncio.create_subprocess_exec(
            "arecord", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            chunk = await asyncio.wait_for(proc.stdout.read(CHUNK), timeout=5.0)
            if not chunk:
                break
            # Cut a stalled reader off instead of blocking here forever.
            await asyncio.wait_for(websocket.send_bytes(chunk), timeout=5.0)
    except (WebSocketDisconnect, asyncio.TimeoutError, Exception):
        pass
    finally:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
        # Release the guard (restarts bender-converse) off the event loop.
        await asyncio.to_thread(_lease_cm.__exit__, None, None, None)
