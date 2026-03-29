"""BenderPi Web UI — FastAPI application."""
import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import Body, Depends, FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web.auth import require_pin, verify_pin
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import cfg

_WEB_DIR = os.path.dirname(os.path.abspath(__file__))

_BASE_DIR = os.path.dirname(os.path.dirname(_WEB_DIR))
_DIST_DIR = os.path.join(_BASE_DIR, "web", "dist")
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
_CLIP_CATEGORIES_PATH = os.path.join(_BASE_DIR, "speech", "clip_categories.json")
_CLIP_LABELS_PATH = os.path.join(_BASE_DIR, "speech", "clip_labels.json")
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


def _normalise_entry(entry):
    """Normalise an index.json entry to (file_path, label_or_None)."""
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        return entry.get("file", ""), entry.get("label")
    return str(entry), None


def _load_clip_categories() -> dict[str, str]:
    """Load clip_categories.json → {filename: category} lookup."""
    try:
        with open(_CLIP_CATEGORIES_PATH, "r") as f:
            data = json.load(f)
        return {fname: cat for cat, fnames in data.items() for fname in fnames}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_clip_labels() -> dict[str, str]:
    """Load clip_labels.json → {filename: label} lookup."""
    try:
        with open(_CLIP_LABELS_PATH, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _get_clips() -> list[dict]:
    """Discover clips from WAV dir + index.json, merge with favourites."""
    favs = set(_load_favourites())
    clip_cats = _load_clip_categories()
    clip_labels = _load_clip_labels()
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
                    "label": clip_labels.get(fname, name),
                    "category": clip_cats.get(fname, "clips"),
                    "favourite": rel in favs,
                }

    # 2. Read index.json for categorised clips
    try:
        with open(_INDEX_PATH, "r") as f:
            index = json.load(f)
    except (OSError, json.JSONDecodeError):
        index = {}

    for category, entries in index.items():
        if category == "promoted":
            # Promoted entries are objects with pattern+file fields
            if isinstance(entries, list):
                for entry in entries:
                    file_path, label = _normalise_entry(entry)
                    if file_path and file_path not in clips:
                        name = os.path.splitext(os.path.basename(file_path))[0]
                        clips[file_path] = {
                            "path": file_path,
                            "name": name,
                            "label": label or name,
                            "category": "promoted",
                            "favourite": file_path in favs,
                        }
        elif isinstance(entries, list):
            for entry in entries:
                file_path, label = _normalise_entry(entry)
                if file_path and file_path not in clips:
                    name = os.path.splitext(os.path.basename(file_path))[0]
                    clips[file_path] = {
                        "path": file_path,
                        "name": name,
                        "label": label or name,
                        "category": category,
                        "favourite": file_path in favs,
                    }
        elif isinstance(entries, dict):
            # personal sub-keys
            for sub_key, entry in entries.items():
                file_path, label = _normalise_entry(entry)
                if file_path and file_path not in clips:
                    clips[file_path] = {
                        "path": file_path,
                        "name": sub_key,
                        "label": label or sub_key,
                        "category": category,
                        "favourite": file_path in favs,
                    }

    return list(clips.values())


# ── Core Endpoints ──────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/actions/session-status", dependencies=[Depends(require_pin)])
async def session_status_detail():
    if os.path.exists(cfg.session_file):
        try:
            with open(cfg.session_file) as f:
                return json.load(f)
        except Exception:
            return {"active": False}
    return {"active": False}


@app.post("/api/actions/end-session", dependencies=[Depends(require_pin)])
async def end_session():
    if not os.path.exists(cfg.session_file):
        return {"status": "no_session"}
    with open(cfg.end_session_file, "w") as f:
        f.write("")
    with open(cfg.abort_file, "w") as f:
        f.write("")
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


async def _puppet_play(wav_path: str) -> None:
    """Play a WAV, pausing bender-converse first if it's running.

    The WM8960 is a single-rate codec. bender-converse locks it at 16 kHz
    (porcupine mic). bender-web needs 44100 Hz for output. Stopping the
    service releases the device so play_oneshot can open at the correct rate.
    """
    import audio
    import leds

    if not _IS_LINUX:
        leds.set_talking()
        await asyncio.to_thread(audio.play_oneshot, wav_path, leds.set_level, leds.all_off)
        return

    # Check current state
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
        # Brief pause so PortAudio/ALSA releases the device
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


@app.post("/api/puppet/speak", dependencies=[Depends(require_pin)])
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
    await _puppet_play(resolved)
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



# ── Timer Endpoints ────────────────────────────────────────────────────────

import timers as timers_mod


@app.get("/api/timers", dependencies=[Depends(require_pin)])
async def list_timers():
    return {"timers": timers_mod.list_timers()}


@app.post("/api/timers", dependencies=[Depends(require_pin)])
async def create_timer_api(body: dict = Body(...)):
    label = body.get("label", "timer")
    if "duration_s" in body:
        timer = timers_mod.create_timer(label, float(body["duration_s"]))
        return {"status": "ok", "timer": timer}
    elif "fires_at" in body:
        fires_at = datetime.fromisoformat(body["fires_at"])
        timer = timers_mod.create_alarm(label, fires_at)
        return {"status": "ok", "timer": timer}
    raise HTTPException(status_code=400, detail="Provide duration_s or fires_at")


@app.delete("/api/timers/{timer_id}", dependencies=[Depends(require_pin)])
async def cancel_timer_api(timer_id: str):
    if timers_mod.cancel_timer(timer_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Timer not found")


@app.post("/api/timers/{timer_id}/dismiss", dependencies=[Depends(require_pin)])
async def dismiss_timer_api(timer_id: str):
    if timers_mod.dismiss_timer(timer_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Timer not found")


@app.post("/api/timers/dismiss-all", dependencies=[Depends(require_pin)])
async def dismiss_all_timers():
    count = timers_mod.dismiss_all_fired()
    return {"status": "ok", "dismissed": count}


# ── Remote Voice Endpoint ───────────────────────────────────────────────────


@app.post("/api/remote/ask", dependencies=[Depends(require_pin)])
async def remote_ask(audio: UploadFile = File(...)):
    """Accept audio from browser, transcribe via Whisper, run pipeline, return WAV as base64."""
    import base64
    import tempfile
    import time

    t_start = time.time()
    audio_bytes = await audio.read()
    if len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio too short")

    ct = (audio.content_type or "").lower()
    fn = (audio.filename or "").lower()
    if "mp4" in ct or "mp4" in fn or "aac" in ct:
        in_suffix = ".mp4"
    elif "ogg" in ct or "ogg" in fn:
        in_suffix = ".ogg"
    else:
        in_suffix = ".webm"

    tmp_in = None
    tmp_wav = None
    resp_wav = None
    resp_is_temp = False

    try:
        # 1. Save upload to temp file
        with tempfile.NamedTemporaryFile(suffix=in_suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_in = f.name
        tmp_wav = tmp_in[: -len(in_suffix)] + "_16k.wav"

        # 2. Convert to 16 kHz mono WAV for Whisper
        conv = await asyncio.to_thread(
            subprocess.run,
            [
                "ffmpeg", "-y", "-i", tmp_in,
                "-ar", "16000", "-ac", "1", "-f", "wav", tmp_wav,
            ],
            capture_output=True,
            timeout=30,
        )
        if conv.returncode != 0:
            raise HTTPException(status_code=500, detail="Audio conversion failed")

        # 3. Transcribe
        import stt as _stt
        transcript = await asyncio.to_thread(_stt.transcribe_file, tmp_wav)

        # 4. Resolve response through the normal pipeline
        if not transcript:
            import tts_generate as _tts
            resp_text = "I heard absolutely nothing. Either speak up or stop wasting my circuits."
            resp_wav = await asyncio.to_thread(_tts.speak, resp_text)
            resp_is_temp = True
            resp_intent = "SILENCE"
        else:
            from responder import Responder
            from ai_response import AIResponder
            resp = await asyncio.to_thread(
                Responder().get_response, transcript, AIResponder()
            )
            resp_wav = resp.wav_path
            resp_is_temp = resp.is_temp
            resp_text = resp.text
            resp_intent = resp.intent

        # 5. Encode WAV as base64 and return
        with open(resp_wav, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()

        return {
            "transcript": transcript,
            "response_text": resp_text,
            "intent": resp_intent,
            "audio_b64": audio_b64,
            "duration_ms": round((time.time() - t_start) * 1000),
        }

    finally:
        for p in [tmp_in, tmp_wav]:
            try:
                if p:
                    os.unlink(p)
            except OSError:
                pass
        if resp_is_temp and resp_wav:
            try:
                os.unlink(resp_wav)
            except OSError:
                pass




# ── Camera Stream ─────────────────────────────────────────────────────────────

def _check_camera() -> bool:
    """Returns True if picamera2 and camera hardware are both available."""
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.close()
        return True
    except Exception:
        return False


@app.get("/api/puppet/camera/status", dependencies=[Depends(require_pin)])
async def puppet_camera_status():
    available = await asyncio.to_thread(_check_camera)
    return {"available": available}


@app.get("/api/puppet/camera/stream")
async def puppet_camera_stream(pin: str = ""):
    """MJPEG stream from the Pi Camera. PIN via query param (img src limitation)."""
    if not verify_pin(pin):
        raise HTTPException(status_code=401, detail="Invalid PIN")

    available = await asyncio.to_thread(_check_camera)
    if not available:
        raise HTTPException(status_code=503, detail="Camera not available")

    async def generate():
        from picamera2 import Picamera2
        import io
        cam = Picamera2()
        config = cam.create_video_configuration(
            main={"size": (640, 480), "format": "RGB888"}
        )
        cam.configure(config)
        cam.start()
        try:
            from PIL import Image
            while True:
                frame = await asyncio.to_thread(cam.capture_array)
                buf = io.BytesIO()
                Image.fromarray(frame).save(buf, format="JPEG", quality=70)
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                    + buf.getvalue()
                    + b"\r\n"
                )
                await asyncio.sleep(0.1)  # ~10 fps
        except asyncio.CancelledError:
            pass
        finally:
            cam.stop()
            cam.close()

    from fastapi.responses import StreamingResponse
    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")

# ── Puppet Mic Stream ──────────────────────────────────────────────────────────

@app.websocket("/ws/puppet/mic")
async def puppet_mic_ws(websocket: WebSocket):
    """Stream ambient mic audio to operator (PCM 16 kHz mono S16_LE).

    PIN is passed as a query param because WebSocket handshakes cannot carry
    custom headers from browser clients.
    """
    pin = websocket.query_params.get("pin", "")
    if not verify_pin(pin):
        await websocket.close(code=4001)
        return

    await websocket.accept()

    CHUNK = 4096  # ~128 ms at 16 kHz S16_LE mono

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "arecord", "-D", "default", "-f", "S16_LE",
            "-r", "16000", "-c", "1", "-",
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

# ── Static files (must be last — catches all unmatched routes) ──

if os.path.isdir(_DIST_DIR):
    app.mount("/", StaticFiles(directory=_DIST_DIR, html=True), name="static")
