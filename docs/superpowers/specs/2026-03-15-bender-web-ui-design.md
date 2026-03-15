# BenderPi Web UI — Design Spec

**Date:** 2026-03-15
**Status:** Draft
**Scope:** Browser-based admin panel and puppet mode for BenderPi. View logs, adjust config, control the service, and speak through Bender in real-time.

---

## 1. Goals

1. **Puppet mode** — type text for Bender to speak immediately via TTS, plus a soundboard of real Bender clips with favourites. For curated interactions with young children.
2. **Ops dashboard** — health overview, performance metrics, usage stats, and watchdog alerts rendered from the existing metrics/logging infrastructure.
3. **Log viewer** — browse conversations, system logs, and metrics with filtering and search. Download raw files.
4. **Config editor** — edit `bender_config.json` and `watchdog_config.json` from the browser. Trigger service actions (restart, refresh briefings, rebuild responses).
5. **Responsive** — works on phone (puppet mode in the room) and desktop (dashboard at the desk).
6. **Simple auth** — PIN-based, single user, home network only.

### Non-goals

- Multi-user accounts or roles
- Real-time streaming of conversation audio
- Editing intent patterns or response text from the UI (use code for that)
- Push notifications or webhooks
- HTTPS (home LAN only — can be added later behind a reverse proxy)

---

## 2. Architecture

### Backend

A **FastAPI** application in `scripts/web/`. Runs as a separate systemd service (`bender-web`) alongside the existing `bender-converse`.

**Why separate service:** The web server and conversation loop share the same Python modules but run in separate processes. This means:
- Puppet mode calls `tts_generate.speak()` + `audio.play_oneshot()` directly — `play_oneshot()` acquires `_lock`, safe even during an active conversation session
- Config reads are safe (read-only). Config writes go to `bender_config.json` on disk — conversation service picks up changes on restart
- Log/metrics files are append-only JSONL — safe for concurrent reads
- A web server crash doesn't take down the voice assistant

**Port:** Configurable via `BENDER_WEB_PORT` env var, default `8080`.

### Frontend

Static HTML/CSS/JS served by FastAPI's `StaticFiles` mount. **No build step, no framework.** Vanilla JS with `fetch()` for API calls.

- Single `index.html` page with four tabs
- Tab switching via JS (no full page reloads)
- CSS variables for dark/light theme toggle
- Responsive via CSS media queries

### Process model

```
┌─────────────────────┐     ┌─────────────────────┐
│  bender-converse    │     │  bender-web         │
│  (systemd service)  │     │  (systemd service)  │
│                     │     │                     │
│  wake_converse.py   │     │  FastAPI (uvicorn)  │
│  ├─ stt.py         │     │  ├─ API routes      │
│  ├─ responder.py   │     │  ├─ static files    │
│  ├─ audio.py       │     │  └─ imports:        │
│  ├─ tts_generate   │     │     tts_generate    │
│  └─ ...            │     │     audio.play_oneshot│
│                     │     │     config, metrics  │
└─────────────────────┘     └─────────────────────┘
         │                           │
         └───── shared modules ──────┘
         └───── shared log files ────┘
         └───── shared config ───────┘
```

---

## 3. API Design

All endpoints are JSON. Every request (except the login page and static files) requires the PIN as an `X-Bender-Pin` header or `?pin=` query param.

### 3.1 Puppet

| Endpoint | Method | Purpose | Request body | Response |
|---|---|---|---|---|
| `/api/puppet/speak` | POST | Generate TTS and play on speaker | `{"text": "..."}` (max 500 chars) | `{"status": "ok", "text": "..."}` |
| `/api/puppet/clip` | POST | Play a WAV clip on speaker | `{"path": "speech/wav/hello.wav"}` | `{"status": "ok", "clip": "..."}` |
| `/api/puppet/clips` | GET | List all available clips with metadata | — | `{"clips": [{"path": "...", "name": "...", "category": "...", "favourite": bool}]}` |
| `/api/puppet/favourite` | POST | Toggle favourite status | `{"path": "...", "favourite": true}` | `{"status": "ok"}` |

**Clip discovery:** Scans `speech/wav/` for WAV files and reads `speech/responses/index.json` to determine categories. Clip names are derived from filenames (strip `.wav`, replace hyphens/underscores with spaces, title case).

**Favourites storage:** `favourites.json` in the project root (committed or gitignored — it's UI preference state). Simple list of paths.

### 3.2 Dashboard

| Endpoint | Method | Purpose | Query params | Response |
|---|---|---|---|---|
| `/api/status` | GET | Current status data as JSON | — | Parsed STATUS.md equivalent as structured JSON |
| `/api/status/refresh` | POST | Regenerate status and return fresh data | — | Same as GET but freshly generated |

### 3.3 Logs

| Endpoint | Method | Purpose | Query params | Response |
|---|---|---|---|---|
| `/api/logs/conversations` | GET | List conversation log files | `?days=7` | `{"files": [{"date": "2026-03-15", "path": "...", "size": 1234}]}` |
| `/api/logs/conversations/{date}` | GET | Sessions and turns for a date | — | `{"events": [...]}` (parsed JSONL) |
| `/api/logs/system` | GET | Tail bender.log | `?lines=200&level=ERROR` | `{"lines": [...]}` |
| `/api/logs/metrics` | GET | Query metrics | `?name=stt_transcribe&hours=24` | `{"events": [...]}` |
| `/api/logs/download/{filename}` | GET | Download raw log file | — | File download |

### 3.4 Config

| Endpoint | Method | Purpose | Request body | Response |
|---|---|---|---|---|
| `/api/config` | GET | Current bender_config.json | — | JSON contents |
| `/api/config` | PUT | Update bender_config.json | Partial JSON overlay | `{"status": "ok", "config": {...}}` |
| `/api/config/watchdog` | GET | Current watchdog_config.json | — | JSON contents |
| `/api/config/watchdog` | PUT | Update watchdog_config.json | Partial JSON overlay | `{"status": "ok", "config": {...}}` |

### 3.5 Actions

| Endpoint | Method | Purpose | Response |
|---|---|---|---|
| `/api/actions/restart` | POST | Restart bender-converse service | `{"status": "ok", "message": "..."}` |
| `/api/actions/refresh-briefings` | POST | Restart service (triggers briefing refresh at start) | `{"status": "ok"}` |
| `/api/actions/prebuild` | POST | Run prebuild_responses.py | `{"status": "ok", "output": "..."}` |
| `/api/actions/generate-status` | POST | Run generate_status.py | `{"status": "ok"}` |
| `/api/actions/service-status` | GET | Check if bender-converse is running | `{"running": true, "uptime": "4d 12h"}` |

---

## 4. UI Pages

### 4.1 Login

Simple full-page form: PIN input + submit button. PIN stored in `sessionStorage`. Redirects to Puppet tab on success. Bender image and "BenderPi" title for branding.

### 4.2 Tab: Puppet Mode

**Text-to-speech input:**
- Large textarea (3-4 rows) with character count (max 500)
- "SPEAK" button — posts to `/api/puppet/speak`, shows spinner during TTS generation + playback, disables button to prevent double-fire
- Status indicator: "Speaking...", "Done", or error message

**Favourites section (always visible at top):**
- Horizontal scrolling row of pill/chip buttons
- Each chip: clip name + play icon. Tap = immediate playback via `/api/puppet/clip`
- Star icon on each to unpin from favourites
- Empty state: "No favourites yet — star clips below to pin them here"

**All clips (collapsible sections below):**
- Grouped by category from index.json: Greeting, Affirmation, Dismissal, Joke, Personal, HA Confirm
- Each category is a collapsible section (click header to expand/collapse)
- Each clip: name + play button + star icon (to add to favourites)
- All sections collapsed by default to keep the page clean

### 4.3 Tab: Dashboard

**Health row:**
- Three traffic-light cards: Errors (24h), API Rate, STT Empty Rate
- Green = within threshold, Amber = warning, Red = error-level alert
- Each card shows the current value and threshold

**Performance row:**
- Cards for: STT Record, STT Transcribe, TTS Generate, API Call, End-to-end
- Each shows 7-day average in ms
- Simple colour coding: green <threshold, amber near threshold, red above

**Usage summary:**
- Sessions count, total turns, local %, API %, error %
- Top intents as a simple horizontal bar or list

**Attention needed:**
- Watchdog alerts rendered as a list with severity badges (info/warning/error)
- Each alert has a message and supporting data

**Refresh button** — top right, calls `/api/status/refresh`

### 4.4 Tab: Logs

**Sub-navigation** (toggle buttons within the tab): Conversations | System | Metrics

**Conversations view:**
- Date picker (last 7 days as buttons, or calendar)
- Session list for selected date — each session shows: session ID, turn count, duration, end reason
- Click session to expand and show turns
- Each turn: user text, intent badge, method badge (colour-coded), response text
- Method colours: `real_clip`=green, `pre_gen_tts`=blue, `handler_*`=teal, `ai_fallback`=orange, `error_fallback`=red

**System log view:**
- Tail of `bender.log` (last 200 lines by default)
- Level filter buttons: DEBUG | INFO | WARNING | ERROR
- Search box (client-side text filter)
- Auto-refresh toggle (polls every 10s when active)

**Metrics view:**
- Dropdown to select metric name (stt_transcribe, tts_generate, ai_api_call, etc.)
- Time range selector (1h, 6h, 24h, 7d)
- Table of matching events: timestamp, name, duration_ms/tags

**Download section:**
- List of available log files with download links

### 4.5 Tab: Config & Actions

**Config editor:**
- Reads `bender_config.json` via GET `/api/config`
- Renders each field as an appropriate input:
  - Numbers: `<input type="number">` with step
  - Booleans: toggle switch
  - Strings: text input
  - `led_colour`: three number inputs (R, G, B) with colour preview swatch
- "Save" button: shows diff of changes before confirming, PUTs the delta

**Watchdog config editor:**
- Same treatment for `watchdog_config.json`
- Fields are all numeric thresholds — number inputs with labels explaining what each does

**Actions:**
- Button grid with confirmation dialogs:
  - Restart Bender → `sudo systemctl restart bender-converse`
  - Refresh Briefings → restart (triggers refresh at start)
  - Rebuild Responses → runs `prebuild_responses.py`
  - Generate Status → runs `generate_status.py`
- Each shows success/error feedback inline
- Service status indicator at the top: running/stopped badge with uptime

---

## 5. Visual Theme

### Colour palette (CSS variables)

**Dark theme (default):**

| Variable | Value | Usage |
|---|---|---|
| `--bg` | `#1a1a2e` | Page background |
| `--bg-card` | `#16213e` | Cards, panels, sections |
| `--bg-input` | `#0f3460` | Input backgrounds |
| `--accent` | `#e94560` | Active tabs, primary buttons, highlights |
| `--text` | `#e0e0e0` | Primary text |
| `--text-muted` | `#888888` | Secondary text |
| `--success` | `#4ecca3` | Health green, real_clip method |
| `--warning` | `#f0a500` | Health amber, warnings |
| `--error` | `#e94560` | Health red, errors (same as accent) |
| `--border` | `#333333` | Subtle borders |

**Light theme:**

| Variable | Value | Usage |
|---|---|---|
| `--bg` | `#f5f5f5` | Page background |
| `--bg-card` | `#ffffff` | Cards, panels |
| `--bg-input` | `#e8e8e8` | Input backgrounds |
| `--accent` | `#d63447` | Slightly darker red for contrast on white |
| `--text` | `#1a1a2e` | Primary text |
| `--text-muted` | `#666666` | Secondary text |
| `--success` | `#2d8f6f` | Darker green for white bg |
| `--warning` | `#c48200` | Darker amber |
| `--error` | `#d63447` | Same as accent |
| `--border` | `#dddddd` | Subtle borders |

### Theme toggle

- Sun/moon icon button in the header bar, next to the Bender logo
- Toggles `data-theme="light"` attribute on `<body>`
- Preference persisted in `localStorage` — survives page reloads and sessions

### Branding

- **Favicon:** `icons8-futurama-bender.svg` from `assets/`
- **Header:** Small Bender PNG (`Bender_Rodriguez.png`) at ~32px height next to "BenderPi" text, left-aligned in the tab bar
- **Login page:** Larger Bender image centred above the PIN input

### Typography

- Body: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
- Monospace (logs, metrics, code): `'SF Mono', 'Fira Code', 'Cascadia Code', monospace`
- Base size: 16px, scale down to 14px for dense data (log lines, metric tables)

### Responsive breakpoints

- `<768px` (mobile): tabs scroll horizontally, single column, soundboard clips stack, log table collapses to card view
- `>=768px` (desktop): wider panels, soundboard grid (3-4 columns), side-by-side layouts where useful

---

## 6. Authentication

### PIN-based auth

- **PIN source:** `BENDER_WEB_PIN` in `.env`. Default: `2904`.
- **Login flow:** Unauthenticated `index.html` loads, shows login overlay. User enters PIN, JS stores it in `sessionStorage`, sends test request to `/api/status`. On 200, hides login overlay and shows the app. On 401, shows error.
- **Request auth:** Every API `fetch()` includes `X-Bender-Pin` header. File download links append `?pin=` as query param.
- **Middleware:** FastAPI dependency that checks the header/param on every `/api/` route. Returns 401 JSON on failure. Static files are unprotected (they're just HTML/CSS/JS — the data is behind the API).
- **No rate limiting** — single user, home network. If PIN is compromised, change it in `.env` and restart.

---

## 7. File Structure

### New files

```
scripts/web/
├── __init__.py
├── app.py                — FastAPI app: mounts static, defines API routes
├── auth.py               — PIN auth middleware
├── static/
│   ├── index.html        — single page with all four tabs
│   ├── style.css         — all styles (dark + light themes via CSS variables)
│   ├── app.js            — core: tab switching, auth, fetch wrapper, theme toggle
│   ├── puppet.js         — puppet mode: TTS input, soundboard, favourites
│   ├── dashboard.js      — dashboard rendering, status data
│   ├── logs.js           — log viewer, filtering, search
│   ├── config.js         — config editor form, actions
│   └── favicon.svg       — copy of assets/icons8-futurama-bender.svg
└── assets/
    └── bender.png        — copy of assets/Bender_Rodriguez.png (served as static)
```

### New config files

```
favourites.json           — UI favourite clips list (project root, gitignored)
```

### Modified files

| File | Changes |
|---|---|
| `requirements.txt` | Add `fastapi==0.115.0`, `uvicorn==0.32.0` |
| `.env.example` | Add `BENDER_WEB_PIN=2904`, `BENDER_WEB_PORT=8080` |
| `.gitignore` | Add `favourites.json` |
| `scripts/git_pull.sh` | Also restart `bender-web` on deploy |
| `CLAUDE.md` | Add web UI section to project docs |
| `HANDOVER.md` | Note web UI addition |

### New systemd service

`bender-web.service`:
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

---

## 8. Implementation Order

Incremental commits, each functional:

1. **FastAPI skeleton** — app.py, auth.py, static mount, PIN middleware, health check endpoint. Service file.
2. **Frontend shell** — index.html with tab bar, style.css with dark/light themes, app.js with tab switching + auth + theme toggle. Login page.
3. **Puppet mode** — `/api/puppet/*` endpoints + puppet.js. TTS speak, clip listing, playback, favourites.
4. **Dashboard** — `/api/status` endpoint + dashboard.js. Health cards, performance, usage, alerts.
5. **Log viewer** — `/api/logs/*` endpoints + logs.js. Conversations, system log, metrics, downloads.
6. **Config & actions** — `/api/config`, `/api/actions/*` endpoints + config.js. Form editor, action buttons with confirmation.
7. **Polish** — responsive tweaks, error handling, loading states, Bender branding assets, git_pull.sh update, docs update.
