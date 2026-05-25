import asyncio
import json
import os
import subprocess
import sys
import time

from fastapi import APIRouter, Body, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from web.auth import require_pin, verify_pin

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

import audio
import leds
import vision as _vision

_IS_LINUX = os.name != "nt"
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

async def _puppet_play(wav_path: str) -> None:
    """Play a WAV, stopping bender-converse first (WM8960 is single-rate)."""
    if not _IS_LINUX:
        leds.set_talking()
        await asyncio.to_thread(audio.play_oneshot, wav_path, leds.set_level, leds.all_off)
        return

    result = await asyncio.to_thread(
        subprocess.run,
        ["systemctl", "is-active", "bender-converse"],
        capture_output=True, text=True, timeout=5,
    )
    was_running = result.stdout.strip() == "active"

    if was_running:
        await asyncio.to_thread(
            subprocess.run,
            ["sudo", "systemctl", "stop", "bender-converse"],
            capture_output=True, text=True, timeout=15,
        )
        await asyncio.sleep(0.5)

    leds.set_talking()
    try:
        await asyncio.to_thread(audio.play_oneshot, wav_path, leds.set_level, leds.all_off)
    finally:
        if was_running:
            await asyncio.to_thread(
                subprocess.run,
                ["sudo", "systemctl", "start", "bender-converse"],
                capture_output=True, text=True, timeout=15,
            )


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

@router.get("/api/puppet/clips", dependencies=[Depends(require_pin)])
async def puppet_clips():
    return {"clips": _get_clips()}


@router.post("/api/puppet/speak", dependencies=[Depends(require_pin)])
async def puppet_speak(body: dict = Body(...)):
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    if len(text) > 500:
        raise HTTPException(status_code=400, detail="Text too long (max 500 chars)")
    import tts_generate
    wav_path = await asyncio.to_thread(tts_generate.speak, text)
    try:
        await _puppet_play(wav_path)
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
    return {"status": "ok", "text": text}


@router.post("/api/puppet/clip", dependencies=[Depends(require_pin)])
async def puppet_clip(body: dict = Body(...)):
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
    await _puppet_play(resolved)
    return {"status": "ok", "path": path}


@router.post("/api/puppet/favourite", dependencies=[Depends(require_pin)])
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


@router.get("/api/puppet/camera/status", dependencies=[Depends(require_pin)])
async def puppet_camera_status():
    available = await asyncio.to_thread(_check_camera)
    return {"available": available}


@router.get("/api/puppet/camera/stream")
async def puppet_camera_stream(pin: str = ""):
    """MJPEG stream. PIN passed as query param (browser img src limitation)."""
    if not verify_pin(pin):
        raise HTTPException(status_code=401, detail="Invalid PIN")
    available = await asyncio.to_thread(_check_camera)
    if not available:
        raise HTTPException(status_code=503, detail="Camera not available")

    async def _generate():
        import io
        from PIL import Image
        cam = await asyncio.to_thread(_vision.acquire_camera)
        try:
            while True:
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
    """Stream ambient mic audio to operator (PCM 16 kHz mono S16_LE)."""
    pin = websocket.query_params.get("pin", "")
    if not verify_pin(pin):
        await websocket.close(code=4001)
        return

    await websocket.accept()
    CHUNK = 4096
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "arecord", "-D", "default", "-f", "S16_LE", "-r", "16000", "-c", "1", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(CHUNK), timeout=5.0)
            if not chunk:
                break
            await websocket.send_bytes(chunk)
    except (WebSocketDisconnect, asyncio.TimeoutError, Exception):
        pass
    finally:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
