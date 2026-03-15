"""BenderPi Web UI — FastAPI application."""
import asyncio
import json
import os
import re
import subprocess

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from web.auth import require_pin

_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_WEB_DIR, "static")
_ASSETS_DIR = os.path.join(_WEB_DIR, "assets")

_BASE_DIR = os.path.dirname(os.path.dirname(_WEB_DIR))
_FAVOURITES_PATH = os.path.join(_BASE_DIR, "favourites.json")
_INDEX_PATH = os.path.join(_BASE_DIR, "speech", "responses", "index.json")
_WAV_DIR = os.path.join(_BASE_DIR, "speech", "wav")

app = FastAPI(title="BenderPi", docs_url=None, redoc_url=None)


# ── Helpers ─────────────────────────────────────────────


def _load_favourites() -> list[str]:
    """Read favourites.json, return [] on any error."""
    try:
        with open(_FAVOURITES_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return []


def _save_favourites(favs: list[str]) -> None:
    """Write favourites list to favourites.json."""
    with open(_FAVOURITES_PATH, "w") as f:
        json.dump(favs, f, indent=2)


def _get_clips() -> list[dict]:
    """Discover clips from WAV dir + index.json, merge with favourites."""
    favs = set(_load_favourites())
    clips = {}

    # 1. Scan speech/wav/ for raw WAV files
    if os.path.isdir(_WAV_DIR):
        for fname in sorted(os.listdir(_WAV_DIR)):
            if fname.lower().endswith(".wav"):
                rel = "speech/wav/" + fname
                name = os.path.splitext(fname)[0]
                clips[rel] = {
                    "path": rel,
                    "name": name,
                    "category": "clips",
                    "favourite": rel in favs,
                }

    # 2. Read index.json for categorised clips
    try:
        with open(_INDEX_PATH, "r") as f:
            index = json.load(f)
    except (OSError, json.JSONDecodeError):
        index = {}

    for category, entries in index.items():
        if isinstance(entries, list):
            for path in entries:
                if path not in clips:
                    name = os.path.splitext(os.path.basename(path))[0]
                    clips[path] = {
                        "path": path,
                        "name": name,
                        "category": category,
                        "favourite": path in favs,
                    }
        elif isinstance(entries, dict):
            # personal sub-keys
            for sub_key, path in entries.items():
                if path not in clips:
                    clips[path] = {
                        "path": path,
                        "name": sub_key,
                        "category": category,
                        "favourite": path in favs,
                    }

    return list(clips.values())


# ── Core Endpoints ──────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/actions/service-status", dependencies=[Depends(require_pin)])
async def service_status():
    return {"running": True, "uptime": "unknown"}


# ── Puppet Endpoints ───────────────────────────────────


@app.post("/api/puppet/speak", dependencies=[Depends(require_pin)])
async def puppet_speak(body: dict = Body(...)):
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")
    if len(text) > 500:
        raise HTTPException(status_code=400, detail="Text too long (max 500 chars)")
    import tts_generate
    import audio
    wav_path = await asyncio.to_thread(tts_generate.speak, text)
    try:
        await asyncio.to_thread(audio.play_oneshot, wav_path)
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
    return {"status": "ok", "text": text}


@app.post("/api/puppet/clip", dependencies=[Depends(require_pin)])
async def puppet_clip(body: dict = Body(...)):
    path = body.get("path", "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="No path provided")
    if not path.endswith(".wav"):
        raise HTTPException(status_code=400, detail="Path must end in .wav")
    # Path traversal prevention
    resolved = os.path.normpath(os.path.join(_BASE_DIR, path))
    if not resolved.startswith(os.path.normpath(_BASE_DIR)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="Clip not found")
    import audio
    await asyncio.to_thread(audio.play_oneshot, resolved)
    return {"status": "ok", "path": path}


@app.get("/api/puppet/clips", dependencies=[Depends(require_pin)])
async def puppet_clips():
    clips = _get_clips()
    return {"clips": clips}


@app.post("/api/puppet/favourite", dependencies=[Depends(require_pin)])
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


# ── Dashboard Endpoints ─────────────────────────────────


@app.get("/api/status", dependencies=[Depends(require_pin)])
async def get_status():
    from generate_status import generate_dict
    return await asyncio.to_thread(generate_dict)


@app.post("/api/status/refresh", dependencies=[Depends(require_pin)])
async def refresh_status():
    from generate_status import generate_dict, generate
    await asyncio.to_thread(generate)
    return await asyncio.to_thread(generate_dict)


# ── Volume Endpoints ────────────────────────────────────


@app.get("/api/config/volume", dependencies=[Depends(require_pin)])
async def volume_get():
    result = await asyncio.to_thread(
        subprocess.run,
        ["amixer", "-c", "2", "sget", "Speaker"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Could not read volume")
    # Parse percentage from amixer output, e.g. [85%]
    match = re.search(r"\[(\d+)%\]", result.stdout)
    if not match:
        raise HTTPException(status_code=500, detail="Could not parse volume")
    return {"level": int(match.group(1))}


@app.post("/api/config/volume", dependencies=[Depends(require_pin)])
async def volume_set(body: dict = Body(...)):
    level = body.get("level")
    if level is None or not isinstance(level, (int, float)):
        raise HTTPException(status_code=400, detail="level must be a number")
    level = max(0, min(100, int(level)))
    result = await asyncio.to_thread(
        subprocess.run,
        ["amixer", "-c", "2", "sset", "Speaker", f"{level}%"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Could not set volume")
    return {"status": "ok", "level": level}


# ── Static files (must be last — catches all unmatched routes) ──

if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
