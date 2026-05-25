import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from web.auth import require_pin

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
_BASE_DIR = os.path.dirname(_SCRIPTS_DIR)
sys.path.insert(0, _SCRIPTS_DIR)

_LOG_DIR = os.path.join(_BASE_DIR, "logs")
_BENDER_LOG = os.path.join(_LOG_DIR, "bender.log")
_METRICS_LOG = os.path.join(_LOG_DIR, "metrics.jsonl")

router = APIRouter(dependencies=[Depends(require_pin)])


@router.get("/api/logs/conversations")
async def log_conversations_list(days: int = 7):
    files = []
    if os.path.isdir(_LOG_DIR):
        cutoff = datetime.now(tz=timezone.utc).date() - timedelta(days=days - 1)
        for fname in sorted(os.listdir(_LOG_DIR), reverse=True):
            if not fname.endswith(".jsonl") or fname == "metrics.jsonl":
                continue
            date_str = fname[:-6]
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
    return {"dates": [f["date"] for f in files], "files": files}


@router.get("/api/logs/conversations/{date}")
async def log_conversations_date(date: str):
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
    return {"entries": events}


@router.get("/api/logs/system")
async def log_system(lines: int = 200, level: str = ""):
    level = level.upper().strip()
    if level not in {"", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise HTTPException(status_code=400, detail="Invalid level")
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
    if level:
        all_lines = [ln for ln in all_lines if level in ln]
    return {"log": "\n".join(ln.rstrip("\n") for ln in all_lines[-lines:])}


@router.get("/api/logs/metrics")
async def log_metrics(name: str = "", hours: int = 24):
    if not os.path.isfile(_METRICS_LOG):
        return {"entries": []}
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
                if name and event.get("name") != name:
                    continue
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
    return {"entries": events[-500:]}


@router.get("/api/logs/download/{filename}")
async def log_download(filename: str):
    if not (re.match(r"^[\w.-]+\.jsonl$", filename) or re.match(r"^bender\.log(\.\d+)?$", filename)):
        raise HTTPException(status_code=400, detail="Invalid filename")
    resolved = os.path.normpath(os.path.join(_LOG_DIR, filename))
    if not resolved.startswith(os.path.normpath(_LOG_DIR) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(resolved, filename=filename, media_type="application/octet-stream")
