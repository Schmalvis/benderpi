"""BenderPi Web UI — FastAPI application."""
import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web.auth import require_pin

_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_WEB_DIR, "static")
_ASSETS_DIR = os.path.join(_WEB_DIR, "assets")

_BASE_DIR = os.path.dirname(os.path.dirname(_WEB_DIR))
_FAVOURITES_PATH = os.path.join(_BASE_DIR, "favourites.json")
_INDEX_PATH = os.path.join(_BASE_DIR, "speech", "responses", "index.json")
_WAV_DIR = os.path.join(_BASE_DIR, "speech", "wav")
_LOG_DIR = os.path.join(_BASE_DIR, "logs")
_BENDER_LOG = os.path.join(_LOG_DIR, "bender.log")
_METRICS_LOG = os.path.join(_LOG_DIR, "metrics.jsonl")
_CONFIG_PATH = os.path.join(_BASE_DIR, "bender_config.json")
_WATCHDOG_CONFIG_PATH = os.path.join(_BASE_DIR, "watchdog_config.json")
_VENV_PYTHON = os.path.join(_BASE_DIR, "venv", "bin", "python")
_PREBUILD_SCRIPT = os.path.join(_BASE_DIR, "scripts", "prebuild_responses.py")
_IS_LINUX = os.name != "nt"

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
    if not _IS_LINUX:
        return {"running": False, "uptime": "N/A (not on Pi)"}
    try:
        active = await asyncio.to_thread(
            subprocess.run,
            ["systemctl", "is-active", "bender-converse"],
            capture_output=True, text=True, timeout=5,
        )
        running = active.stdout.strip() == "active"
        uptime_str = "unknown"
        if running:
            ts_result = await asyncio.to_thread(
                subprocess.run,
                ["systemctl", "show", "bender-converse", "--property=ActiveEnterTimestamp"],
                capture_output=True, text=True, timeout=5,
            )
            # Parse ActiveEnterTimestamp=Thu 2025-01-01 12:00:00 UTC
            ts_line = ts_result.stdout.strip()
            if "=" in ts_line:
                ts_val = ts_line.split("=", 1)[1].strip()
                if ts_val:
                    uptime_str = ts_val
        return {"running": running, "uptime": uptime_str}
    except Exception:
        return {"running": False, "uptime": "N/A (not on Pi)"}


# ── Config Endpoints ───────────────────────────────


def _load_json_file(path: str) -> dict:
    """Read a JSON file, return {} on any error."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json_file(path: str, data: dict) -> None:
    """Write dict as JSON to file."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


@app.get("/api/config", dependencies=[Depends(require_pin)])
async def config_get():
    return _load_json_file(_CONFIG_PATH)


@app.put("/api/config", dependencies=[Depends(require_pin)])
async def config_put(body: dict = Body(...)):
    current = _load_json_file(_CONFIG_PATH)
    current.update(body)
    _save_json_file(_CONFIG_PATH, current)
    return {"status": "ok", "config": current}


@app.get("/api/config/watchdog", dependencies=[Depends(require_pin)])
async def config_watchdog_get():
    return _load_json_file(_WATCHDOG_CONFIG_PATH)


@app.put("/api/config/watchdog", dependencies=[Depends(require_pin)])
async def config_watchdog_put(body: dict = Body(...)):
    current = _load_json_file(_WATCHDOG_CONFIG_PATH)
    current.update(body)
    _save_json_file(_WATCHDOG_CONFIG_PATH, current)
    return {"status": "ok", "config": current}


# ── Action Endpoints ──────────────────────────────


@app.post("/api/actions/restart", dependencies=[Depends(require_pin)])
async def action_restart():
    if not _IS_LINUX:
        return {"status": "ok", "message": "Simulated restart (not on Pi)"}
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["sudo", "systemctl", "restart", "bender-converse"],
            capture_output=True, text=True, timeout=30,
        )
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/actions/refresh-briefings", dependencies=[Depends(require_pin)])
async def action_refresh_briefings():
    if not _IS_LINUX:
        return {"status": "ok", "message": "Simulated refresh (not on Pi)"}
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["sudo", "systemctl", "restart", "bender-converse"],
            capture_output=True, text=True, timeout=30,
        )
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/actions/prebuild", dependencies=[Depends(require_pin)])
async def action_prebuild():
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [_VENV_PYTHON, _PREBUILD_SCRIPT],
            capture_output=True, text=True, timeout=120,
            cwd=_BASE_DIR,
        )
        return {
            "status": "ok" if result.returncode == 0 else "error",
            "output": result.stdout + result.stderr,
            "returncode": result.returncode,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/actions/generate-status", dependencies=[Depends(require_pin)])
async def action_generate_status():
    from generate_status import generate
    await asyncio.to_thread(generate)
    return {"status": "ok"}


@app.post("/api/actions/toggle-mode", dependencies=[Depends(require_pin)])
async def action_toggle_mode(body: dict = Body(...)):
    mode = body.get("mode", "").strip()
    if mode not in ("puppet_only", "converse"):
        raise HTTPException(status_code=400, detail="mode must be 'puppet_only' or 'converse'")
    if not _IS_LINUX:
        return {"status": "ok", "mode": mode, "message": "Simulated (not on Pi)"}
    try:
        if mode == "puppet_only":
            cmd = ["sudo", "systemctl", "stop", "bender-converse"]
        else:
            cmd = ["sudo", "systemctl", "start", "bender-converse"]
        await asyncio.to_thread(
            subprocess.run, cmd,
            capture_output=True, text=True, timeout=15,
        )
        return {"status": "ok", "mode": mode}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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


# ── Log Endpoints ───────────────────────────────────────────────


@app.get("/api/logs/conversations", dependencies=[Depends(require_pin)])
async def log_conversations_list(days: int = 7):
    """List conversation JSONL files (excluding metrics.jsonl)."""
    files = []
    if os.path.isdir(_LOG_DIR):
        cutoff = datetime.now(tz=timezone.utc).date() - timedelta(days=days - 1)
        for fname in sorted(os.listdir(_LOG_DIR), reverse=True):
            if not fname.endswith(".jsonl") or fname == "metrics.jsonl":
                continue
            date_str = fname[:-6]  # strip ".jsonl"
            try:
                file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_date < cutoff:
                continue
            fpath = os.path.join(_LOG_DIR, fname)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                size = 0
            files.append({"date": date_str, "filename": fname, "size": size})
    return {"files": files}


@app.get("/api/logs/conversations/{date}", dependencies=[Depends(require_pin)])
async def log_conversations_date(date: str):
    """Parse JSONL for a given date, return events with computed session durations."""
    # Validate date format
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format (expected YYYY-MM-DD)")
    fpath = os.path.join(_LOG_DIR, f"{date}.jsonl")
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="Log file not found")
    events = []
    session_starts: dict[str, str] = {}
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type", "")
                sid = event.get("session_id", "")
                if etype == "session_start" and sid:
                    session_starts[sid] = event.get("ts", "")
                elif etype == "session_end" and sid:
                    start_ts = session_starts.get(sid, "")
                    end_ts = event.get("ts", "")
                    if start_ts and end_ts:
                        try:
                            t0 = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                            t1 = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
                            event = dict(event, duration_s=round((t1 - t0).total_seconds(), 1))
                        except (ValueError, TypeError):
                            pass
                events.append(event)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"events": events}


@app.get("/api/logs/system", dependencies=[Depends(require_pin)])
async def log_system(lines: int = 200, level: str = ""):
    """Read rotated bender.log files, filter by level, return last N lines."""
    level = level.upper().strip()
    valid_levels = {"", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if level not in valid_levels:
        raise HTTPException(status_code=400, detail="Invalid level")
    # Collect from rotated files in chronological order (oldest first)
    log_paths = []
    for suffix in (".3", ".2", ".1", ""):
        p = _BENDER_LOG + suffix if suffix else _BENDER_LOG
        if os.path.isfile(p):
            log_paths.append(p)
    all_lines = []
    for p in log_paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                all_lines.extend(f.readlines())
        except OSError:
            pass
    # Filter by level if specified
    if level:
        all_lines = [ln for ln in all_lines if level in ln]
    # Return last N lines
    result = [ln.rstrip("\n") for ln in all_lines[-lines:]]
    return {"lines": result}


@app.get("/api/logs/metrics", dependencies=[Depends(require_pin)])
async def log_metrics(name: str = "", hours: int = 24):
    """Filter metrics.jsonl by name and time window, return last 500 events."""
    if not os.path.isfile(_METRICS_LOG):
        return {"events": []}
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    events = []
    try:
        with open(_METRICS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Filter by name
                if name and event.get("name") != name:
                    continue
                # Filter by time
                ts_str = event.get("ts", "")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass
                events.append(event)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"events": events[-500:]}


@app.get("/api/logs/download/{filename}", dependencies=[Depends(require_pin)])
async def log_download(filename: str):
    """Download a raw log file. Only allows *.jsonl or bender.log* filenames."""
    # Security: whitelist allowed filename patterns
    if not (re.match(r"^[\w.-]+\.jsonl$", filename) or re.match(r"^bender\.log(\.\d+)?$", filename)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Resolve and verify path stays within _LOG_DIR
    resolved = os.path.normpath(os.path.join(_LOG_DIR, filename))
    if not resolved.startswith(os.path.normpath(_LOG_DIR) + os.sep) and resolved != os.path.normpath(_LOG_DIR):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(resolved, filename=filename, media_type="application/octet-stream")


# ── Static files (must be last — catches all unmatched routes) ──

if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
