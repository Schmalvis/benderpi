# BenderPi UI Redesign — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the BenderPi web UI with a Futurama-themed "robot control panel" aesthetic, persistent sidebar with quick controls, end-session capability, and improved mobile responsiveness.

**Architecture:** This is a frontend-heavy rewrite. The existing FastAPI backend gets two new endpoints (end-session, session-status via file-based IPC). The HTML/CSS is rewritten completely for Theme C. JS files are updated for sidebar logic, session polling, quotes, and reorganised config groups. All existing API endpoints remain unchanged.

**Tech Stack:** Vanilla HTML/CSS/JS (no framework), FastAPI (existing), file-based IPC between web server and conversation loop processes.

**Spec:** `docs/superpowers/specs/2026-03-17-ui-redesign-design.md`

**Key constraints:**
- Commits auto-deploy to Pi within 5 minutes
- Web server and conversation loop are separate processes — IPC via files in project root
- All DOM construction must use safe methods (createElement, textContent) — never innerHTML with dynamic data
- Scan-line overlay at z-index 1 (decorative, must not block clicks)
- Session-status polling only after login, cleared on logout
- Sidebar toggles use optimistic updates with revert on error
- Preserve Remote tab if it exists in current index.html
- `.end_session` and `.session_active.json` must be gitignored

**Testing:**
- Backend IPC endpoints tested via FastAPI TestClient
- Frontend tested manually in browser
- Existing tests must continue to pass

---

## Chunk 1: Backend — End Session IPC + Session Status

### Task 1: File-based IPC in wake_converse.py + API endpoints

**Files:**
- Modify: `scripts/wake_converse.py`
- Modify: `scripts/web/app.py`
- Modify: `.gitignore`
- Create: `tests/test_web_session.py`

- [ ] **Step 1: Write tests**

`tests/test_web_session.py`:
```python
"""Tests for session status and end-session IPC."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)

def auth():
    return {"X-Bender-Pin": PIN}

def test_session_status_inactive():
    """When no .session_active.json exists, session is inactive."""
    path = os.path.join(_BASE_DIR, ".session_active.json")
    if os.path.exists(path):
        os.unlink(path)
    client = get_client()
    resp = client.get("/api/actions/session-status", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["active"] is False

def test_session_status_active(tmp_path):
    """When .session_active.json exists, session is active."""
    # Write a session file to the expected location
    path = os.path.join(_BASE_DIR, ".session_active.json")
    try:
        with open(path, "w") as f:
            json.dump({"active": True, "session_id": "test123", "turns": 2}, f)
        client = get_client()
        resp = client.get("/api/actions/session-status", headers=auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["session_id"] == "test123"
    finally:
        if os.path.exists(path):
            os.unlink(path)

def test_end_session_no_active():
    """End session when nothing is active returns no_session."""
    path = os.path.join(_BASE_DIR, ".session_active.json")
    if os.path.exists(path):
        os.unlink(path)
    flag = os.path.join(_BASE_DIR, ".end_session")
    if os.path.exists(flag):
        os.unlink(flag)
    client = get_client()
    resp = client.post("/api/actions/end-session", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_session"

def test_end_session_creates_flag():
    """End session when active creates .end_session flag file."""
    session_path = os.path.join(_BASE_DIR, ".session_active.json")
    flag_path = os.path.join(_BASE_DIR, ".end_session")
    try:
        with open(session_path, "w") as f:
            json.dump({"active": True, "session_id": "test456", "turns": 1}, f)
        client = get_client()
        resp = client.post("/api/actions/end-session", headers=auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert os.path.exists(flag_path)
    finally:
        for p in [session_path, flag_path]:
            if os.path.exists(p):
                os.unlink(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_session.py -v`

- [ ] **Step 3: Add session-status and end-session endpoints to app.py**

Read `scripts/web/app.py` first. Add:

```python
_SESSION_FILE = os.path.join(_BASE_DIR, ".session_active.json")
_END_SESSION_FILE = os.path.join(_BASE_DIR, ".end_session")

@app.get("/api/actions/session-status", dependencies=[Depends(require_pin)])
async def session_status():
    if os.path.exists(_SESSION_FILE):
        try:
            with open(_SESSION_FILE) as f:
                return json.load(f)
        except Exception:
            return {"active": False}
    return {"active": False}

@app.post("/api/actions/end-session", dependencies=[Depends(require_pin)])
async def end_session():
    if not os.path.exists(_SESSION_FILE):
        return {"status": "no_session"}
    with open(_END_SESSION_FILE, "w") as f:
        f.write("")
    return {"status": "ok"}
```

Replace the existing stub `service_status` endpoint (if it references the same path) or ensure the new `session_status` route doesn't conflict.

- [ ] **Step 4: Update wake_converse.py with session file IPC**

Read `scripts/wake_converse.py`. Add:

**Module-level constants:**
```python
_SESSION_FILE = os.path.join(BASE_DIR, ".session_active.json")
_END_SESSION_FILE = os.path.join(BASE_DIR, ".end_session")
```

**In `run_session()`, at session start (after `session_log.session_start()`):**
```python
# Write session-active file for web UI
_write_session_file(session_log.session_id, 0)
```

**After each turn (after `session_log.log_turn(...)`):**
```python
_write_session_file(session_log.session_id, session_log.turn)
```

**Before each `stt.listen_and_transcribe()` call (at top of the while loop, before `leds.set_listening`):**
```python
# Check for remote end-session request
if os.path.exists(_END_SESSION_FILE):
    try:
        os.unlink(_END_SESSION_FILE)
    except OSError:
        pass
    log.info("Session ended by remote request")
    if not (cfg.silent_wakeword and cfg.led_listening_enabled):
        clip = responder.pick_clip("DISMISSAL")
        if clip and os.path.exists(clip):
            leds.set_talking()
            audio.play(clip)
    leds.all_off()
    session_log.session_end("remote_end")
    metrics.count("session", event="end", turns=session_log.turn, reason="remote_end")
    _remove_session_file()
    audio.close_session()
    return
```

**At all session exit points (timeout, dismissal, and the new remote_end), call:**
```python
_remove_session_file()
```

**Helper functions:**
```python
def _write_session_file(session_id: str, turns: int):
    try:
        with open(_SESSION_FILE, "w") as f:
            json.dump({
                "active": True,
                "session_id": session_id,
                "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "turns": turns,
            }, f)
    except OSError as e:
        log.warning("Failed to write session file: %s", e)

def _remove_session_file():
    try:
        if os.path.exists(_SESSION_FILE):
            os.unlink(_SESSION_FILE)
    except OSError:
        pass
    # Also clean up any stale end-session flag
    try:
        if os.path.exists(_END_SESSION_FILE):
            os.unlink(_END_SESSION_FILE)
    except OSError:
        pass
```

- [ ] **Step 5: Add IPC files to .gitignore**

Append:
```
.end_session
.session_active.json
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add scripts/wake_converse.py scripts/web/app.py .gitignore tests/test_web_session.py
git commit -m "Add end-session IPC and session-status API for web UI

File-based IPC between web server and conversation loop:
- .session_active.json written at session start, deleted at end
- .end_session flag written by web API, checked by conversation loop

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Chunk 2: CSS Rewrite — Theme C

### Task 2: Complete CSS rewrite with Futurama theme

**Files:**
- Modify: `scripts/web/static/style.css`

This is a complete rewrite of `style.css`. Read the current file first to understand all the classes used by the JS modules (puppet, dashboard, logs, config) — those class names must be preserved or the JS will break.

- [ ] **Step 1: Read current style.css and catalogue all class names used by JS**

Read `scripts/web/static/style.css`, `puppet.js`, `dashboard.js`, `logs.js`, `config.js` to identify every CSS class referenced from JS.

- [ ] **Step 2: Rewrite style.css**

Complete rewrite implementing Theme C from the spec:

**Structure (in order):**
1. CSS custom properties (dark theme default + light theme override)
2. Reset + base styles (body, *, fonts)
3. Scan-line overlay (z-index: 1, dark theme only)
4. Utilities (.hidden, .error, .success, .text-muted, .spinner)
5. Header (glowing avatar, animated status dot, brand title + subtitle)
6. Sidebar (fixed left, 60px wide, vertical controls, status dot)
7. Tab bar (horizontal, scrollable, blue accent active state)
8. Main content area (offset by sidebar width)
9. Footer (quote + branding)
10. Cards (gradient background, blue top-edge glow, border)
11. Section headers ("> " prefix via ::before)
12. Buttons (.btn-primary blue, .btn-danger red, .btn-action)
13. Forms (inputs, textareas, range sliders, toggle switches, dropdowns)
14. Colour pickers (RGB inputs + swatch)
15. Collapsible sections (details/summary styled)
16. Badges (.badge-info, .badge-warning, .badge-error, .badge-success)
17. Method colour classes (.method-real_clip, .method-ai_fallback, etc.)
18. Puppet tab (speak form, favourites row, clip grid)
19. Dashboard (status banner, health/perf/usage cards, alerts, intent bars)
20. Logs (segmented control, session cards, turn rows, log viewer, metrics table)
21. Config (grouped sub-panels, action grid)
22. FAB + bottom sheet (mobile only)
23. Login overlay
24. Responsive (@media max-width: 768px)
25. Animations (avatar-glow, pulse, scan)

**Key theme values:**
- `--bg: #0a0e1a`, `--bg-card: #12162a`, `--bg-sidebar: #0d1020`, `--bg-input: #1a1e30`
- `--accent: #4a9eff`, `--accent-red: #e94560`
- `--text: #e0e0e0`, `--text-muted: #6a6a9a`
- `--success: #4ecca3`, `--warning: #f0a500`
- `--border: #2a2a4a`, `--glow: rgba(74, 158, 255, 0.15)`
- Light theme: scan lines disabled, glows become shadows, gradient cards become flat

**Preserve all class names used by JS** — add new classes for sidebar, FAB, bottom sheet, status banner, etc. but don't rename existing ones that puppet.js/dashboard.js/logs.js/config.js reference.

- [ ] **Step 3: Commit**

```bash
git add scripts/web/static/style.css
git commit -m "Rewrite CSS: Futurama Theme C with scan lines, glows, sidebar, FAB

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Chunk 3: HTML + Core JS Restructure

### Task 3: Rewrite index.html with sidebar layout

**Files:**
- Modify: `scripts/web/static/index.html`

- [ ] **Step 1: Read current index.html to identify all tab panels and script tags**

Note any Remote tab or other tabs beyond the base 4.

- [ ] **Step 2: Rewrite index.html**

New structure:
```
- Login overlay (Bender image, PIN input, "Bite my shiny metal app" button)
- App container:
  - Header (glowing avatar, "BenderPi" + "Bending Unit 22 — Online", status dot, theme toggle, logout)
  - Sidebar (volume slider, LED toggle, puppet-only toggle, silent wake toggle, end session button, service status)
  - Tab bar (Puppet, Dashboard, Logs, Config [+ Remote if it exists])
  - Main content (tab panels)
  - Footer (Bender quote, "BenderPi" text)
- FAB button (mobile, hidden on desktop)
- Bottom sheet (mobile, hidden by default)
- Script tags for all JS files
```

All sidebar controls are static HTML with IDs — the JS wires them up.

- [ ] **Step 3: Commit**

```bash
git add scripts/web/static/index.html
git commit -m "Restructure HTML: sidebar, themed header/footer, FAB for mobile

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Update app.js — sidebar, quotes, session polling, FAB

**Files:**
- Modify: `scripts/web/static/app.js`

- [ ] **Step 1: Read current app.js to understand the window.bender API**

Identify all exported functions and the onTabInit pattern.

- [ ] **Step 2: Update app.js**

**Add to app.js:**

1. **Bender quotes object** — `BENDER_QUOTES` with pools per context (footer, puppet, dashboard, logs, config, empty, end_session). Utility function `getQuote(context)` returns a random one.

2. **Footer quote rotation** — update footer quote on tab switch via `getQuote(activeTab)`.

3. **Sidebar logic:**
   - Volume slider: GET `/api/config/volume` on init, POST on change (debounced 300ms)
   - LED listening toggle: reads `led_listening_enabled` from GET `/api/config`, toggles via PUT `/api/config` with optimistic update + revert on error
   - Puppet-only toggle: POST `/api/actions/toggle-mode` with optimistic update
   - Silent wake toggle: reads `silent_wakeword` from config, greyed out when LED toggle is off, toggles via PUT `/api/config`
   - End session button: POST `/api/actions/end-session`, only visible when session is active

4. **Session status polling:**
   - `_sessionPollInterval` — set to `setInterval(pollSession, 3000)` AFTER successful login
   - Cleared on logout (`clearInterval`)
   - `pollSession()` calls GET `/api/actions/session-status`, updates: end-session button visibility, dashboard status banner (if dashboard is active), sidebar status dot

5. **FAB + bottom sheet (mobile):**
   - FAB click toggles bottom sheet visibility
   - Bottom sheet backdrop click closes it
   - Bottom sheet contains cloned/mirrored sidebar controls (or the sidebar itself repositioned via CSS)

6. **Update header subtitle** — "Online" when service running, "Offline" when stopped, "In Conversation" when session active.

**Preserve:** All existing exports on `window.bender` (api, apiJson, apiDownload, el, onTabInit).

- [ ] **Step 3: Commit**

```bash
git add scripts/web/static/app.js
git commit -m "Update app.js: sidebar controls, session polling, quotes, FAB logic

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Chunk 4: Tab JS Updates

### Task 5: Update puppet.js — speech rate, themed styling

**Files:**
- Modify: `scripts/web/static/puppet.js`

- [ ] **Step 1: Read current puppet.js**

- [ ] **Step 2: Update puppet.js**

Changes:
- **Remove** volume slider (moved to sidebar)
- **Add** speech rate slider (0.5–2.0, step 0.1) next to the SPEAK button. On change, PUT `/api/config` with `{"speech_rate": value}`. Show label like "Speed: 1.2x".
- **Update** section headers to use themed console-style (the CSS handles this via `section-header` class)
- **Update** SPEAK button to use `.btn-primary` class (glows blue)
- Ensure all DOM construction still uses `window.bender.el()` — no innerHTML

- [ ] **Step 3: Commit**

```bash
git add scripts/web/static/puppet.js
git commit -m "Update puppet: add speech rate slider, remove volume (moved to sidebar)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Update dashboard.js — status banner, themed cards

**Files:**
- Modify: `scripts/web/static/dashboard.js`

- [ ] **Step 1: Read current dashboard.js**

- [ ] **Step 2: Update dashboard.js**

Changes:
- **Add** status banner at top: shows Bender state from session-status polling data (exposed via `window.bender.getSessionState()`). States: "Idle", "Listening", "In Conversation (turn N)", "Puppet Only Mode". Rotating Bender quote from `getQuote('dashboard')`.
- **Update** card rendering to use themed classes (`.card` with gradient background gets applied by CSS)
- **Make** Alerts, Recent Errors, and Git Log sections collapsible (`<details>` elements, collapsed by default)
- All DOM construction via `window.bender.el()`

- [ ] **Step 3: Commit**

```bash
git add scripts/web/static/dashboard.js
git commit -m "Update dashboard: status banner, collapsible sections, themed cards

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Update logs.js — segmented controls, themed badges

**Files:**
- Modify: `scripts/web/static/logs.js`

- [ ] **Step 1: Read current logs.js**

- [ ] **Step 2: Update logs.js**

Changes:
- **Replace** sub-view toggle buttons with segmented control styling (CSS class `.segmented-control` with `.seg-btn` children)
- **Update** session cards to be `<details>` elements (collapsible)
- **Update** method badges to use themed badge classes
- **Ensure** empty states use Bender quotes from `getQuote('empty')`
- **Add** download buttons that use `window.bender.apiDownload()`

- [ ] **Step 3: Commit**

```bash
git add scripts/web/static/logs.js
git commit -m "Update logs: segmented controls, collapsible sessions, themed badges

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Reorganise config.js — grouped sub-panels, new field types

**Files:**
- Modify: `scripts/web/static/config.js`

- [ ] **Step 1: Read current config.js to understand CONFIG_GROUPS structure**

- [ ] **Step 2: Update config.js**

Changes:
- **Reorganise** CONFIG_GROUPS:
  - New "Voice" group: `speech_rate` (range, 0.5–2.0, step 0.1), `thinking_sound` (bool) — **remove these from "Audio" group**
  - "Audio" group: `silence_pre`, `silence_post`, `silence_timeout` only
  - "LEDs" group: add `led_listening_colour` (colour), `led_talking_colour` (colour), `led_listening_enabled` (bool), `silent_wakeword` (bool with dependency note)
  - "Logging" group: change `log_level` from `type: "string"` to `type: "select"` with `options: ["DEBUG", "INFO", "WARNING", "ERROR"]`

- **Add** `select` field type to `buildFieldInput`:
  ```javascript
  if (field.type === 'select') {
      const select = el('select', {className: 'config-select'});
      (field.options || []).forEach(opt => {
          const option = el('option', {value: opt, textContent: opt});
          if (opt === currentValue) option.selected = true;
          select.appendChild(option);
      });
      select.addEventListener('change', () => markDirty(field.key, select.value));
      return select;
  }
  ```

- **Make** each config group a collapsible `<details>` element (open by default on desktop, closed on mobile — CSS handles this via media query `details { open }` or JS checks width on init)

- **Update** service actions section to include the themed button grid and status badge

- **Ensure** `silent_wakeword` toggle is visually greyed out and shows a tooltip when `led_listening_enabled` is false

- [ ] **Step 3: Run all tests**

Run: `python -m pytest tests/ -v`

- [ ] **Step 4: Commit**

```bash
git add scripts/web/static/config.js
git commit -m "Reorganise config: Voice group, LED colours, select dropdown, collapsible panels

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Chunk 5: Final Polish

### Task 9: Update .gitignore, HANDOVER.md, manual test

**Files:**
- Modify: `HANDOVER.md`

- [ ] **Step 1: Update HANDOVER.md**

Add to Recent Decisions:
- UI redesigned with Futurama Theme C (scan lines, glows, gradient cards, animated status)
- Added persistent sidebar with quick controls
- End-session via file-based IPC between web server and conversation loop

Add to Current Priorities:
- Test UI on phone (mobile FAB/bottom sheet)
- Tune scan-line opacity and glow intensity based on real device viewing

- [ ] **Step 2: Manual test checklist**

Start the server locally and verify:
```bash
BENDER_WEB_PIN=2904 python -m uvicorn scripts.web.app:app --reload --port 8080
```

Check:
- [ ] Login page shows themed Bender image
- [ ] Dark theme: scan lines visible, cards have gradient, avatar glows
- [ ] Light theme: no scan lines, clean white cards, shadows instead of glows
- [ ] Sidebar: all 5 controls visible on desktop
- [ ] Mobile (<768px): sidebar hidden, FAB visible, bottom sheet opens
- [ ] Puppet tab: speak + speech rate slider + soundboard
- [ ] Dashboard tab: status banner + health/perf/usage cards
- [ ] Logs tab: segmented control, collapsible sessions
- [ ] Config tab: grouped sub-panels, LED section with new colour fields
- [ ] End session button: hidden when no session, visible when session file exists
- [ ] Tab switch updates footer quote
- [ ] All existing tests still pass

- [ ] **Step 3: Commit**

```bash
git add HANDOVER.md
git commit -m "Update HANDOVER.md with UI redesign notes

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Summary

| Task | What | Commit |
|---|---|---|
| 1 | Backend: end-session IPC + session-status API | "Add end-session IPC and session-status API" |
| 2 | CSS rewrite: Theme C | "Rewrite CSS: Futurama Theme C" |
| 3 | HTML restructure: sidebar, header, footer, FAB | "Restructure HTML: sidebar, themed header/footer, FAB" |
| 4 | app.js: sidebar controls, quotes, polling, FAB | "Update app.js: sidebar, session polling, quotes, FAB" |
| 5 | puppet.js: speech rate, remove volume | "Update puppet: speech rate, remove volume" |
| 6 | dashboard.js: status banner, collapsible sections | "Update dashboard: status banner, collapsible sections" |
| 7 | logs.js: segmented controls, themed badges | "Update logs: segmented controls, themed badges" |
| 8 | config.js: grouped panels, select dropdown, LED fields | "Reorganise config: Voice group, LED colours, select, collapsible" |
| 9 | Polish: HANDOVER.md, manual test | "Update HANDOVER.md with UI redesign notes" |
