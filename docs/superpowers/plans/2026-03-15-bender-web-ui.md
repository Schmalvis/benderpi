# BenderPi Web UI — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser-based admin panel and puppet mode for BenderPi — view logs, adjust config, control the service, and speak through Bender in real-time.

**Architecture:** FastAPI backend serves a REST API + static HTML/CSS/JS frontend. Runs as a separate systemd service (`bender-web`) alongside `bender-converse`. Both share the same Python modules. All blocking audio/TTS calls wrapped in `asyncio.to_thread()`.

**Tech Stack:** Python 3.13, FastAPI, uvicorn, vanilla HTML/CSS/JS (no framework, no build step). Existing BenderPi modules: config, logger, metrics, tts_generate, audio, watchdog, generate_status, conversation_log.

**Spec:** `docs/superpowers/specs/2026-03-15-bender-web-ui-design.md`

**Key constraints:**
- Commits to `main` auto-deploy to the Pi within 5 minutes
- `audio.play_oneshot()` and `tts_generate.speak()` are blocking — must use `asyncio.to_thread()` in FastAPI endpoints
- PIN auth via `BENDER_WEB_PIN` env var (default `2904`, `.env.example` uses `CHANGE_ME` placeholder)
- Sudoers entry required for service restart actions (documented in spec section 7)
- `generate_status.py` needs a `generate_dict()` function (currently only writes Markdown)
- Session duration computed from `session_start.ts - session_end.ts` (no explicit duration field)
- Log rotation: `bender.log` + `.1`, `.2`, `.3` — system log endpoint must read across rotated files
- Favourites stored in `favourites.json` at project root (gitignored), resolved via `_BASE_DIR`

**Security notes:**
- All dynamic content rendered via `textContent` or safe DOM methods — never raw `innerHTML` with user/API data
- PIN transmitted via `X-Bender-Pin` header only (not query params) to avoid log exposure
- File download uses JS `fetch()` + blob URL (PIN stays in header)
- Path traversal prevention on clip playback and log download endpoints

**Testing strategy:**
- Backend API tests using FastAPI's `TestClient` (synchronous, no uvicorn needed)
- Mock `audio.play_oneshot()` and `tts_generate.speak()` in tests (hardware-dependent)
- Frontend is manually tested in the browser (no JS test framework — YAGNI for a single-user admin panel)
- Tests run on the dev machine (Windows), not the Pi

---

## Chunk 1: Backend Foundation

### Task 1: Add dependencies and create FastAPI skeleton

**Files:**
- Modify: `requirements.txt`
- Create: `scripts/web/__init__.py`
- Create: `scripts/web/auth.py`
- Create: `scripts/web/app.py`
- Create: `tests/test_web_auth.py`

- [ ] **Step 1: Add FastAPI and uvicorn to requirements.txt**

Append to `requirements.txt`:
```
# Web UI
fastapi==0.115.0
uvicorn==0.32.0
```

- [ ] **Step 2: Write auth tests**

`tests/test_web_auth.py`:
```python
"""Tests for web UI PIN authentication."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

def test_valid_pin_passes(monkeypatch):
    monkeypatch.setenv("BENDER_WEB_PIN", "1234")
    from web.auth import verify_pin
    assert verify_pin("1234") is True

def test_invalid_pin_fails(monkeypatch):
    monkeypatch.setenv("BENDER_WEB_PIN", "1234")
    from web.auth import verify_pin
    assert verify_pin("wrong") is False

def test_default_pin(monkeypatch):
    monkeypatch.delenv("BENDER_WEB_PIN", raising=False)
    from web.auth import verify_pin
    assert verify_pin("2904") is True

def test_api_rejects_without_pin():
    from fastapi.testclient import TestClient
    from web.app import app
    resp = TestClient(app).get("/api/actions/service-status")
    assert resp.status_code == 401

def test_api_accepts_valid_pin(monkeypatch):
    monkeypatch.setenv("BENDER_WEB_PIN", "9999")
    from fastapi.testclient import TestClient
    from web.app import app
    resp = TestClient(app).get(
        "/api/actions/service-status",
        headers={"X-Bender-Pin": "9999"},
    )
    assert resp.status_code != 401
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_auth.py -v`
Expected: FAIL — no `web` module

- [ ] **Step 4: Create scripts/web/__init__.py**

Empty file.

- [ ] **Step 5: Create scripts/web/auth.py**

```python
"""PIN-based authentication for BenderPi web UI."""
import os
from fastapi import Request, HTTPException

DEFAULT_PIN = "2904"


def get_pin() -> str:
    return os.environ.get("BENDER_WEB_PIN", DEFAULT_PIN)


def verify_pin(pin: str) -> bool:
    return pin == get_pin()


async def require_pin(request: Request):
    """FastAPI dependency — checks X-Bender-Pin header."""
    pin = request.headers.get("X-Bender-Pin", "")
    if not verify_pin(pin):
        raise HTTPException(status_code=401, detail="Invalid PIN")
```

- [ ] **Step 6: Create scripts/web/app.py (skeleton)**

```python
"""BenderPi Web UI — FastAPI application."""
import os
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles

from web.auth import require_pin

_WEB_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_WEB_DIR, "static")
_ASSETS_DIR = os.path.join(_WEB_DIR, "assets")

app = FastAPI(title="BenderPi", docs_url=None, redoc_url=None)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/actions/service-status", dependencies=[Depends(require_pin)])
async def service_status():
    return {"running": True, "uptime": "unknown"}


# Static files (must be last)
if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_web_auth.py -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add requirements.txt scripts/web/ tests/test_web_auth.py
git commit -m "Add FastAPI skeleton with PIN auth middleware"
```

---

### Task 2: Frontend shell — HTML, CSS, tab navigation, login, theme toggle

**Files:**
- Create: `scripts/web/static/index.html`
- Create: `scripts/web/static/style.css`
- Create: `scripts/web/static/app.js`
- Create: `scripts/web/static/puppet.js` (placeholder)
- Create: `scripts/web/static/dashboard.js` (placeholder)
- Create: `scripts/web/static/logs.js` (placeholder)
- Create: `scripts/web/static/config.js` (placeholder)
- Create: `scripts/web/static/favicon.svg` (copy from assets)
- Create: `scripts/web/assets/bender.png` (copy from assets)

- [ ] **Step 1: Copy assets**

```bash
mkdir -p scripts/web/static scripts/web/assets
cp assets/icons8-futurama-bender.svg scripts/web/static/favicon.svg
cp assets/Bender_Rodriguez.png scripts/web/assets/bender.png
```

- [ ] **Step 2: Create index.html**

Single-page HTML with:
- Login overlay: centred Bender image, PIN input, submit button, error area
- Header bar: Bender image (32px) + "BenderPi" + tab buttons + theme toggle (sun/moon)
- Four tab panels (Puppet, Dashboard, Logs, Config) — only active one visible
- Script tags for all JS files at bottom

Key structure:
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BenderPi</title>
    <link rel="icon" href="/favicon.svg">
    <link rel="stylesheet" href="/style.css">
</head>
<body data-theme="dark">
    <div id="login-overlay">...</div>
    <div id="app" class="hidden">
        <header>...</header>
        <main>
            <div id="tab-puppet" class="tab-panel active"></div>
            <div id="tab-dashboard" class="tab-panel"></div>
            <div id="tab-logs" class="tab-panel"></div>
            <div id="tab-config" class="tab-panel"></div>
        </main>
    </div>
    <script src="/app.js"></script>
    <script src="/puppet.js"></script>
    <script src="/dashboard.js"></script>
    <script src="/logs.js"></script>
    <script src="/config.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create style.css**

Complete CSS with:
- CSS custom properties for dark theme (default) and light theme (`[data-theme="light"]`)
- Dark palette: `--bg: #1a1a2e`, `--bg-card: #16213e`, `--bg-input: #0f3460`, `--accent: #e94560`, `--text: #e0e0e0`, `--text-muted: #888`, `--success: #4ecca3`, `--warning: #f0a500`, `--error: #e94560`, `--border: #333`
- Light palette: `--bg: #f5f5f5`, `--bg-card: #fff`, `--accent: #d63447`, `--text: #1a1a2e`, etc.
- Login overlay, header, tab bar, tab panels, cards, forms, buttons, badges
- Collapsible `<details>` styling for clip categories
- Responsive breakpoint at 768px
- Utility classes: `.hidden`, `.error`, `.success`, `.spinner`
- Method colour classes for log badges
- Monospace for log/metric content

- [ ] **Step 4: Create app.js**

Core JS handling:
- PIN storage in `sessionStorage`, `api()` fetch wrapper adding `X-Bender-Pin` header
- Login flow: submit PIN, test with `/api/actions/service-status`, show/hide overlay
- Tab switching: click handlers on tab buttons, show/hide panels, call `initPuppet()` etc.
- Theme toggle: swap `data-theme` attribute, persist in `localStorage`
- Auto-login if PIN in sessionStorage

**Important:** Use `textContent` for all user/API-sourced text. Use `document.createElement()` for building DOM elements. Never assign untrusted strings to `innerHTML`.

- [ ] **Step 5: Create placeholder JS files**

`puppet.js`, `dashboard.js`, `logs.js`, `config.js` — each containing just:
```javascript
// Implemented in Task N
```

- [ ] **Step 6: Manual test**

```bash
BENDER_WEB_PIN=2904 python -m uvicorn scripts.web.app:app --reload --port 8080
```
Verify: login page shows, PIN works, tabs switch, theme toggles.

- [ ] **Step 7: Commit**

```bash
git add scripts/web/
git commit -m "Add frontend shell: login, tabs, dark/light theme, Bender branding"
```

---

## Chunk 2: Puppet Mode

### Task 3: Puppet API endpoints + frontend

**Files:**
- Modify: `scripts/web/app.py` (add puppet routes)
- Modify: `scripts/web/static/puppet.js`
- Create: `tests/test_web_puppet.py`

- [ ] **Step 1: Write puppet API tests**

`tests/test_web_puppet.py`:
```python
"""Tests for puppet mode API."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"

def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)

def auth():
    return {"X-Bender-Pin": PIN}

def test_speak_returns_ok():
    client = get_client()
    with patch("tts_generate.speak", return_value="/tmp/test.wav"), \
         patch("audio.play_oneshot"), \
         patch("os.unlink"):
        resp = client.post("/api/puppet/speak",
                          json={"text": "hello"},
                          headers=auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

def test_speak_rejects_long_text():
    client = get_client()
    resp = client.post("/api/puppet/speak",
                      json={"text": "x" * 501},
                      headers=auth())
    assert resp.status_code == 400

def test_clips_returns_list():
    client = get_client()
    resp = client.get("/api/puppet/clips", headers=auth())
    assert resp.status_code == 200
    data = resp.json()
    assert "clips" in data
    assert isinstance(data["clips"], list)

def test_favourite_toggle():
    client = get_client()
    resp = client.post("/api/puppet/favourite",
                      json={"path": "speech/wav/hello.wav", "favourite": True},
                      headers=auth())
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests — should fail**

Run: `python -m pytest tests/test_web_puppet.py -v`

- [ ] **Step 3: Add puppet routes to app.py**

Add these endpoints to `scripts/web/app.py`:

- `POST /api/puppet/speak` — validates text (non-empty, max 500 chars), calls `tts_generate.speak()` + `audio.play_oneshot()` via `asyncio.to_thread()`, cleans up temp WAV in finally block
- `POST /api/puppet/clip` — validates path (must end in `.wav`, must exist under `_BASE_DIR`), calls `audio.play_oneshot()` via `asyncio.to_thread()`. **Path traversal prevention:** ensure `os.path.realpath(full_path).startswith(os.path.realpath(_BASE_DIR))`
- `GET /api/puppet/clips` — scans `speech/wav/` for WAV files, reads `index.json` for categories, merges with favourites from `favourites.json`. Returns structured list.
- `POST /api/puppet/favourite` — toggles favourite status in `favourites.json`

Helper functions: `_load_favourites()`, `_save_favourites()`, `_get_clips()`. Resolve `favourites.json` via `_BASE_DIR`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_web_puppet.py -v`
Expected: All pass

- [ ] **Step 5: Implement puppet.js**

Build the puppet mode UI using safe DOM methods:
- **Speak section:** textarea with char counter, SPEAK button, status indicator. On click: POST to `/api/puppet/speak`, disable button during request, show status.
- **Favourites section:** horizontal pill buttons for starred clips. Click = play via `/api/puppet/clip`. Unstar button on each.
- **All clips section:** collapsible `<details>` elements per category. Each clip has play button + star toggle.

All text content set via `textContent`. DOM elements built with `document.createElement()`. Clip names derived from filenames (strip .wav, replace hyphens/underscores with spaces, title case).

- [ ] **Step 6: Run all tests, manual test in browser**

Run: `python -m pytest tests/ -v`
Then start server and test puppet mode.

- [ ] **Step 7: Commit**

```bash
git add scripts/web/ tests/test_web_puppet.py
git commit -m "Add puppet mode: TTS speak, soundboard with favourites"
```

---

## Chunk 3: Dashboard

### Task 4: Dashboard API + frontend

**Files:**
- Modify: `scripts/generate_status.py` (add `generate_dict()`)
- Modify: `scripts/web/app.py` (add dashboard routes)
- Modify: `scripts/web/static/dashboard.js`
- Create: `tests/test_web_dashboard.py`

- [ ] **Step 1: Add generate_dict() to generate_status.py**

Read `scripts/generate_status.py`. Add a `generate_dict()` function that returns structured data as a Python dict (health, performance averages, usage counts, alerts, recent errors, git log). Refactor the existing `generate()` to call `generate_dict()` internally.

The dict structure:
```python
{
    "health": {"errors_7d": int, "alert_count": int},
    "performance": {
        "stt_record_ms": float|None,
        "stt_transcribe_ms": float|None,
        "tts_generate_ms": float|None,
        "ai_api_call_ms": float|None,
        "audio_play_ms": float|None,
        "response_total_ms": float|None,
    },
    "usage": {
        "sessions": int, "turns": int, "local": int, "api": int,
        "errors": int, "local_pct": int, "top_intents": dict,
    },
    "alerts": [{"severity": str, "check": str, "message": str, "data": dict}],
    "recent_errors": [str],
    "git_log": str,
}
```

- [ ] **Step 2: Write dashboard API test**

`tests/test_web_dashboard.py`:
```python
"""Tests for dashboard API."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"

def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)

def test_status_returns_structured_data():
    client = get_client()
    resp = client.get("/api/status", headers={"X-Bender-Pin": PIN})
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert "performance" in data
    assert "usage" in data
    assert "alerts" in data
```

- [ ] **Step 3: Add dashboard routes to app.py**

- `GET /api/status` — calls `generate_dict()` via `asyncio.to_thread()`, returns JSON
- `POST /api/status/refresh` — calls `generate()` (writes STATUS.md) then `generate_dict()`, returns JSON

- [ ] **Step 4: Implement dashboard.js**

Build dashboard using safe DOM methods:
- **Health row:** 3 cards with traffic-light colours (green/amber/red based on alert severity)
- **Performance row:** cards showing average ms for each metric (N/A if no data)
- **Usage summary:** sessions, turns, local/API/error percentages
- **Alerts list:** severity badges (info/warning/error) with messages
- **Refresh button:** calls POST, shows spinner, re-renders

- [ ] **Step 5: Run tests, commit**

```bash
python -m pytest tests/ -v
git add scripts/generate_status.py scripts/web/ tests/test_web_dashboard.py
git commit -m "Add dashboard: health, performance, usage, alerts from metrics"
```

---

## Chunk 4: Logs, Config, Actions, Polish

### Task 5: Log viewer API + frontend

**Files:**
- Modify: `scripts/web/app.py` (add log routes)
- Modify: `scripts/web/static/logs.js`

- [ ] **Step 1: Add log routes to app.py**

- `GET /api/logs/conversations?days=7` — lists JSONL files, excludes metrics.jsonl
- `GET /api/logs/conversations/{date}` — parses JSONL, returns events. Session duration computed from `session_start.ts - session_end.ts` for matching session_id
- `GET /api/logs/system?lines=200&level=ERROR` — reads across rotated log files (`bender.log`, `.1`, `.2`, `.3`), filters by level, returns last N lines
- `GET /api/logs/metrics?name=stt_transcribe&hours=24` — filters metrics.jsonl by name and time range
- `GET /api/logs/download/{filename}` — validates filename pattern (only `.jsonl` or `bender.log*`), returns `FileResponse`. **Path traversal prevention:** ensure resolved path is within `_LOG_DIR`.

- [ ] **Step 2: Implement logs.js**

Three sub-views toggled by buttons:
- **Conversations:** date buttons, session list, expandable turns with method badges (colour-coded via CSS classes)
- **System:** scrollable log viewer, level filter buttons, search input (client-side filter)
- **Metrics:** dropdown for metric name, time range buttons, table of events
- **Downloads:** list files with download buttons (fetch + blob URL)

All rendered with safe DOM methods.

- [ ] **Step 3: Commit**

```bash
git add scripts/web/
git commit -m "Add log viewer: conversations, system log, metrics, downloads"
```

---

### Task 6: Config editor and actions API + frontend

**Files:**
- Modify: `scripts/web/app.py` (add config and action routes)
- Modify: `scripts/web/static/config.js`

- [ ] **Step 1: Add config and action routes to app.py**

Config:
- `GET /api/config` — reads `bender_config.json`
- `PUT /api/config` — merges partial JSON overlay into `bender_config.json`
- `GET /api/config/watchdog` — reads `watchdog_config.json`
- `PUT /api/config/watchdog` — merges partial JSON overlay

Actions:
- `GET /api/actions/service-status` — runs `systemctl is-active bender-converse` + `systemctl show` for uptime via `asyncio.to_thread(subprocess.run, ...)`
- `POST /api/actions/restart` — `sudo systemctl restart bender-converse`
- `POST /api/actions/refresh-briefings` — same as restart (briefings refresh on start)
- `POST /api/actions/prebuild` — runs `venv/bin/python scripts/prebuild_responses.py`
- `POST /api/actions/generate-status` — calls `generate()` from generate_status

- [ ] **Step 2: Implement config.js**

Two sections built with safe DOM methods:
- **Config editor:** loads JSON, renders form with appropriate inputs (number/text/boolean toggle/colour picker for led_colour). Save shows diff, confirms, PUTs delta.
- **Actions:** button grid with confirmation dialogs. Service status badge. Success/error feedback.

- [ ] **Step 3: Run all tests, commit**

```bash
python -m pytest tests/ -v
git add scripts/web/
git commit -m "Add config editor and service actions with confirmation dialogs"
```

---

### Task 7: Deployment files and docs update

**Files:**
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `scripts/git_pull.sh`
- Create: `systemd/bender-web.service`
- Modify: `CLAUDE.md`
- Modify: `HANDOVER.md`

- [ ] **Step 1: Update .env.example**

Add:
```
# Web UI
BENDER_WEB_PIN=CHANGE_ME
BENDER_WEB_PORT=8080
```

- [ ] **Step 2: Update .gitignore**

Add: `favourites.json`

- [ ] **Step 3: Update git_pull.sh**

After the existing service restart, add conditional restart for bender-web:
```bash
if systemctl is-active --quiet bender-web; then
    sudo systemctl restart bender-web
    echo "Web UI restarted."
fi
```

- [ ] **Step 4: Create systemd/bender-web.service**

```ini
[Unit]
Description=BenderPi Web UI
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/bender
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/home/pi/bender/.env
ExecStart=/home/pi/bender/venv/bin/uvicorn scripts.web.app:app --host 0.0.0.0 --port ${BENDER_WEB_PORT:-8080}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Update CLAUDE.md**

Add "Web UI" section: how to run locally, service setup, sudoers requirement, API overview, static frontend location.

- [ ] **Step 6: Update HANDOVER.md**

Add web UI to recent decisions and current priorities (deploy service, set up sudoers, run prebuild).

- [ ] **Step 7: Run all tests, commit**

```bash
python -m pytest tests/ -v
git add .env.example .gitignore scripts/git_pull.sh systemd/ CLAUDE.md HANDOVER.md
git commit -m "Add deployment files, systemd service, docs for web UI"
```

---

## Summary

| Task | What | Commit |
|---|---|---|
| 1 | FastAPI skeleton + PIN auth | "Add FastAPI skeleton with PIN auth middleware" |
| 2 | Frontend shell: HTML, CSS, tabs, login, theme | "Add frontend shell: login, tabs, dark/light theme, Bender branding" |
| 3 | Puppet mode: TTS speak + soundboard + favourites | "Add puppet mode: TTS speak, soundboard with favourites" |
| 4 | Dashboard: health, performance, usage, alerts | "Add dashboard: health, performance, usage, alerts from metrics" |
| 5 | Log viewer: conversations, system, metrics, downloads | "Add log viewer: conversations, system log, metrics, downloads" |
| 6 | Config editor + service actions | "Add config editor and service actions with confirmation dialogs" |
| 7 | Polish: deployment, systemd, docs | "Add deployment files, systemd service, docs for web UI" |
