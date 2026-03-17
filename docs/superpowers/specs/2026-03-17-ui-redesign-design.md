# BenderPi Web UI Redesign — Design Spec

**Date:** 2026-03-17
**Status:** Reviewed
**Scope:** Full visual redesign of the BenderPi web UI. Futurama-themed "robot control panel" aesthetic, reorganised layout with persistent sidebar, collapsible sections, mobile-optimised, context-aware Bender quotes. Also adds end-session control and session status API.

---

## 1. Goals

1. **Futurama-themed aesthetic** — immersive "robot control panel" feel with scan lines, glowing accents, gradient cards, animated status indicators. Theme C from the brainstorm.
2. **Persistent sidebar** — quick controls (volume, LED toggle, puppet-only, silent wake, end session, service status) always accessible without tab switching.
3. **Better grouping** — config fields grouped logically with collapsible sub-panels. All tabs use collapsible sections to keep the view clean.
4. **Mobile-first** — sidebar becomes a FAB (floating action button) with bottom sheet on narrow screens. Tabs scroll. Config sub-panels auto-collapsed.
5. **End session control** — stop Bender mid-conversation from the UI.
6. **Context-aware personality** — rotating Bender quotes matched to the active tab/context.

### Non-goals

- No new API endpoints beyond end-session and session-status
- No changes to backend logic (responder, intent, metrics, etc.)
- No new JS framework or build tooling

---

## 2. Layout

### Desktop (>=768px)

```
┌──────────────────────────────────────────────────────────────┐
│ 🤖 BenderPi                                                 │
│ Bending Unit 22 — Online         ● status    ☀️ theme  ⏏ out│
├────────┬─────────────────────────────────────────────────────┤
│        │  [ Puppet ] [ Dashboard ] [ Logs ] [ Config ]       │
│ SIDE   ├─────────────────────────────────────────────────────┤
│ BAR    │                                                     │
│        │                                                     │
│ 🔊 Vol │               Active tab content                    │
│ 🔵 LED │                                                     │
│ 🎭 Pup │               (full width, scrollable)              │
│ 🔇 Sil │                                                     │
│        │                                                     │
│ ⏹ End  │                                                     │
│        │                                                     │
│ ● Run  │                                                     │
├────────┴─────────────────────────────────────────────────────┤
│ "I'm 40% user interface!" — Bender                 BenderPi │
└──────────────────────────────────────────────────────────────┘
```

- **Header:** Bender avatar (glowing border, pulse animation) + "BenderPi" title + "Bending Unit 22 — Online/Offline" subtitle + status dot + theme toggle + logout
- **Sidebar:** ~60px wide, icons + compact labels. Fixed position, does not scroll with content.
- **Tabs:** Horizontal bar below header, right of sidebar. Active tab has blue accent border + background highlight.
- **Content:** Fills remaining space. Scrollable.
- **Footer:** Context-aware Bender quote + "BenderPi" branding. Slim.

### Mobile (<768px)

- **Header:** Condensed — avatar + "BenderPi" only. Theme toggle + logout as icons.
- **Sidebar:** Hidden. Replaced by a **floating action button** (bottom-left, 56px circle, blue with robot icon). Tap opens a **bottom sheet** sliding up with all sidebar controls arranged in a 2-column grid.
- **Tabs:** Horizontal scroll, full width below header.
- **Content:** Full width, single column.
- **Footer:** Hidden (saves screen space).

---

## 3. Sidebar Controls

The sidebar is the "always accessible" control surface. All controls work without switching tabs.

| Control | Type | Behaviour |
|---|---|---|
| **Volume** | Vertical range slider (0–100) | POST `/api/config/volume` on change (debounced). Shows percentage label. |
| **LED Listening** | Toggle switch | Toggles `led_listening_enabled` in config. Shows blue dot indicator when active. |
| **Puppet Only** | Toggle switch | POST `/api/actions/toggle-mode`. Shows mask icon when active. Stops/starts bender-converse. |
| **Silent Wake** | Toggle switch | Toggles `silent_wakeword` in config. Greyed out + tooltip when LED Listening is off (dependency). |
| **End Session** | Red button | POST `/api/actions/end-session`. Only visible/enabled when a conversation is active. Pulses red. Hidden when idle. |
| **Service Status** | Status dot | Green (running) / Red (stopped) / Grey (unknown). Label below: "Running" / "Stopped". Polled every 10s. |

**Sidebar toggle persistence:** LED, puppet-only, and silent wake toggles write to `bender_config.json` via PUT `/api/config` (same as the config editor). Changes take effect immediately for LED/silent (config is read per-session), and via service stop/start for puppet-only.

**Toggle state management:** Toggles update optimistically (flip the visual state immediately on click). If the PUT fails, revert the visual state and show a brief error toast. Volume slider is debounced (300ms) and fire-and-forget (no revert on error — the physical volume is the source of truth).

---

## 4. New Backend Endpoints

### 4.1 End Session — `POST /api/actions/end-session`

Since the web server and conversation loop run in **separate processes**, this uses file-based IPC (simple, no dependencies).

**File paths (relative to `_BASE_DIR` / project root):**
- End session flag: `_BASE_DIR/.end_session` (presence = end requested)
- Session status: `_BASE_DIR/.session_active.json` (presence = session active)

Both files are in the project root (alongside `.env`), gitignored.

**Conversation loop (`wake_converse.py`):**
- At session start: writes `.session_active.json` with `{"active": true, "session_id": "...", "started": "...", "turns": 0}`
- After each turn: updates the `turns` count in `.session_active.json`
- Before each `stt.listen_and_transcribe()` call: checks `os.path.exists(_END_SESSION_FILE)`. If found, deletes the file, plays a dismissal clip (or silent if `silent_wakeword` mode), logs `session_end(reason="remote_end")`, and breaks.
- At session end (any reason): deletes `.session_active.json`

**Web API (`app.py`):**
- `POST /api/actions/end-session`: writes an empty `.end_session` file. Returns `{"status": "ok"}`. If no session is active (`.session_active.json` doesn't exist), returns `{"status": "no_session"}`.
- `GET /api/actions/session-status`: reads `.session_active.json` if it exists. Returns its contents. If missing, returns `{"active": false}`.

### 4.2 Session Status — `GET /api/actions/session-status`

Returns whether a conversation is currently active and basic session info.

**Implementation:** `wake_converse.py` writes a `session_active.json` file at session start (with session_id, start timestamp, turn count) and deletes it at session end. The web API reads this file.

```json
{"active": true, "session_id": "a1b2c3d4", "started": "2026-03-17T10:30:00Z", "turns": 3}
```

When the file doesn't exist: `{"active": false}`.

**Polling:** The sidebar JS polls this endpoint every 3 seconds to update the End Session button visibility and the Dashboard status banner. **Important:** Polling must only start AFTER successful login (inside the `showApp()` / post-login flow in `app.js`). Must not run while the login overlay is showing, or 401 responses will trigger the auto-logout handler. The poll interval should be cleared on logout.

---

## 5. Colour Palette

### Dark theme (default)

| Variable | Value | Usage |
|---|---|---|
| `--bg` | `#0a0e1a` | Page background |
| `--bg-card` | `#12162a` | Cards, panels, collapsible headers |
| `--bg-sidebar` | `#0d1020` | Sidebar background |
| `--bg-input` | `#1a1e30` | Input fields, textareas |
| `--accent` | `#4a9eff` | Primary blue — tabs, links, glows, active states |
| `--accent-red` | `#e94560` | End session, errors, alerts |
| `--text` | `#e0e0e0` | Primary text |
| `--text-muted` | `#6a6a9a` | Labels, secondary text, quotes |
| `--success` | `#4ecca3` | Health green, service running |
| `--warning` | `#f0a500` | Amber alerts, warning badges |
| `--border` | `#2a2a4a` | Card borders, dividers |
| `--glow` | `rgba(74, 158, 255, 0.15)` | Blue glow on hover, active elements |
| `--scanline` | `rgba(0, 200, 255, 0.015)` | Scan-line overlay stripe colour |

### Light theme

| Variable | Value |
|---|---|
| `--bg` | `#f0f2f5` |
| `--bg-card` | `#ffffff` |
| `--bg-sidebar` | `#e8eaf0` |
| `--bg-input` | `#f5f5f5` |
| `--accent` | `#2a7cdb` |
| `--accent-red` | `#d63447` |
| `--text` | `#1a1a2e` |
| `--text-muted` | `#666680` |
| `--success` | `#2d8f6f` |
| `--warning` | `#c48200` |
| `--border` | `#d0d0e0` |
| `--glow` | `rgba(42, 124, 219, 0.08)` |
| `--scanline` | `none` (disabled in light mode) |

Scan lines disabled. Glows become subtle box-shadows. Gradients flattened to solid colours.

---

## 6. Themed Visual Elements

### Scan-line overlay
CSS pseudo-element on `<body>` — repeating horizontal lines. Subtle, purely decorative. Disabled in light theme.
```css
body[data-theme="dark"]::after {
    content: '';
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: repeating-linear-gradient(0deg, transparent, transparent 2px, var(--scanline) 2px, var(--scanline) 4px);
    pointer-events: none; z-index: 9999;
}
```

### Glowing avatar
Header Bender image with animated blue border glow:
```css
.header-avatar {
    border: 2px solid var(--accent);
    border-radius: 8px;
    box-shadow: 0 0 12px var(--glow);
    animation: avatar-glow 3s ease-in-out infinite alternate;
}
```

### Status dot pulse
Service status and session-active indicators pulse gently:
```css
.status-dot { animation: pulse 2s ease-in-out infinite; }
```

### Card gradient
Cards use a subtle gradient from slightly lighter to base:
```css
.card {
    background: linear-gradient(135deg, var(--bg-card), var(--bg));
    border: 1px solid var(--border);
}
```
Light theme: flat solid `var(--bg-card)` with subtle shadow instead.

### Section headers
Console-style prefix:
```css
.section-header::before { content: '> '; color: var(--accent); }
```

---

## 7. Tab Designs

### 7.1 Puppet Tab

1. **Speak section** — textarea, char counter (500 max), speech rate slider (0.5–2.0x with label), SPEAK button (glows blue, pulses red while speaking)
2. **Favourites** — horizontal scroll row of pill buttons. Tap = play. Star to unpin. Empty state with Bender quote.
3. **All clips** — collapsible `<details>` by category. Grid layout inside. Play + star buttons per clip. All collapsed by default.

Context quotes pool:
- "Shut up baby, I know it"
- "Have you ever tried just turning off the TV?"
- "I'll be whatever I wanna do"
- "My voice is my passport. Verify me."
- "Bite my shiny metal microphone"

### 7.2 Dashboard Tab

1. **Status banner** — full-width. Shows Bender state: "Idle", "Listening", "In Conversation (turn N)", "Puppet Only Mode". Animated dot. Rotating Bender quote.
2. **Health row** — 3 gradient cards: Errors, Local Rate, STT Empty Rate. Traffic-light colours.
3. **Performance row** — 6 metric cards: STT Record, STT Transcribe, TTS Generate, API Call, Audio Play, End-to-end. Show ms averages.
4. **Usage section** — sessions, turns, breakdown. Top intents as horizontal bars.
5. **Alerts** — collapsible. Severity badges.
6. **Recent errors** — collapsible. Last 5 from bender.log.
7. **Recent changes** — collapsible. Git log.
8. **Refresh button** — top-right of tab.

Context quotes pool:
- "I'm 40% diagnostic panel!"
- "My story is a lot like yours, only more interesting"
- "Compare your lives to mine and then kill yourselves"
- "Well, we're boned."
- "This is gonna be fun on a bun!"

### 7.3 Logs Tab

Sub-nav: segmented control (Conversations | System | Metrics).

**Conversations:** Date pills (7 days), collapsible session cards, turns inside with method badges.
**System:** Level filter (segmented), search box, monospace scrollable viewer, auto-refresh toggle.
**Metrics:** Name dropdown, time range pills, table.
**Downloads:** Button row per sub-view.

Context quotes pool:
- "This is the worst kind of discrimination — the kind against me!"
- "Memories. You're talking about memories."
- "My life, and by extension everyone else's, is meaningless."
- "Save it for the memoir, meatbag."
- "I choose to not remember that."

### 7.4 Config Tab

Three collapsible sections:

**Bender Config** — sub-panels by group:
| Group | Fields |
|---|---|
| Voice | `speech_rate` (slider), `thinking_sound` (toggle) — **moved from existing "Audio" group** |
| Audio | `silence_pre`, `silence_post`, `silence_timeout` (number inputs) — `speech_rate` and `thinking_sound` removed from this group |
| STT | `whisper_model` (text), `vad_aggressiveness` (number 0-3) |
| AI Fallback | `ai_model` (text), `ai_max_tokens` (number) |
| Intent | `simple_intent_max_words` (number) |
| Briefings | `weather_ttl`, `news_ttl` (number, seconds) |
| LEDs | `led_brightness` (slider), `led_colour` (RGB + swatch), `led_listening_colour` (RGB), `led_talking_colour` (RGB), `led_listening_enabled` (toggle), `silent_wakeword` (toggle, greyed if LED off) |
| Logging | `log_level` (dropdown: DEBUG, INFO, WARNING, ERROR — add a new `"select"` field type to `buildFieldInput` in `config.js` with `options` array) |

Save button with diff confirmation.

**Watchdog Thresholds** — all numeric, labels explaining each.

**Service Actions** — status badge + button grid (Restart, Refresh Briefings, Rebuild Responses, Generate Status) with confirmation dialogs.

Context quotes pool:
- "I'll build my own config, with blackjack and hookers"
- "Bite my shiny metal preferences"
- "I choose to believe what I was programmed to believe"
- "It's just like the story of the grasshopper and the octopus."
- "Have you ever tried simply turning off the TV, sitting down with your settings, and hitting them?"

---

## 8. Context-Aware Bender Quotes

Stored as a JS object in `app.js`. One pool per context:

```javascript
const BENDER_QUOTES = {
    footer: [...],    // global, shown in footer
    puppet: [...],    // shown when puppet tab is active
    dashboard: [...], // shown when dashboard tab is active
    logs: [...],      // shown when logs tab is active
    config: [...],    // shown when config tab is active
    empty: [...],     // empty states (no data, no sessions, etc.)
    end_session: [...], // shown when session ends
};
```

Footer quote rotates on tab switch. Tab-specific quotes shown in empty states and status banners. Selected randomly from the pool.

---

## 9. Mobile Responsiveness

### Breakpoint: 768px

**Below 768px:**
- Sidebar hidden → FAB (56px circle, bottom-left, `var(--accent)` background, robot/gear icon)
- FAB tap → bottom sheet slides up with all sidebar controls in a 2-column grid
- Bottom sheet has a drag handle and backdrop overlay (tap to dismiss)
- **Z-index stacking order:** scan-line overlay `z-index: 1` (cosmetic, must not block interaction), sidebar `z-index: 10`, FAB `z-index: 100`, bottom sheet backdrop `z-index: 200`, bottom sheet `z-index: 201`, login overlay `z-index: 500`. The scan-line overlay must use `z-index: 1` not `9999` — it's decorative and must never block clicks.
- Tabs: horizontal scroll, full width, smaller font
- Cards: single column stack
- Config sub-panels: all collapsed by default
- Dashboard status banner: condensed (state + dot only, no quote)
- Footer: hidden
- Clip grid: 2 columns instead of 3-4

**Above 768px:**
- Full sidebar visible
- Cards in rows (2-3 per row depending on available width)
- Config sub-panels: open by default
- Footer visible with quote

---

## 10. File Changes

### Modified files

| File | Changes |
|---|---|
| `scripts/web/static/index.html` | New layout: header, sidebar, tabs, footer. FAB for mobile. |
| `scripts/web/static/style.css` | Complete rewrite: Theme C palette, scan lines, glows, gradients, sidebar, FAB, bottom sheet, responsive. Dark + light themes. |
| `scripts/web/static/app.js` | Add: sidebar logic, FAB/bottom sheet, Bender quotes, session status polling, end session call. Update: tab init, theme toggle. |
| `scripts/web/static/puppet.js` | Add: speech rate slider. Remove: volume slider (moved to sidebar). Restyle with themed classes. |
| `scripts/web/static/dashboard.js` | Add: status banner with session state. Restyle: gradient cards, collapsible sections. |
| `scripts/web/static/logs.js` | Restyle: segmented controls, themed cards/badges. |
| `scripts/web/static/config.js` | Reorganise: grouped sub-panels with collapsible headers. Add: log_level dropdown. Restyle. |
| `scripts/web/app.py` | Add: `POST /api/actions/end-session`, `GET /api/actions/session-status`. |
| `scripts/wake_converse.py` | Add: `_end_session_flag` file-based IPC, `session_active.json` write/delete, check flag in conversation loop. |

### Gitignore additions

Add to `.gitignore`:
```
.end_session
.session_active.json
```

### Preservation note

If a Remote tab and `remote.js` exist in the current `index.html`, they must survive the rewrite. Include the Remote tab in the new tab bar and preserve its script tag.

### No new files

All changes are modifications to existing files. The quotes object goes into `app.js`.

---

## 11. Implementation Order

1. **Backend: end-session + session-status** — file-based IPC in wake_converse.py, new API endpoints in app.py
2. **CSS rewrite** — Theme C palette, scan lines, glows, gradients, sidebar layout, FAB, bottom sheet, responsive breakpoints, dark + light themes
3. **HTML restructure** — new index.html with sidebar, header, footer, FAB
4. **app.js update** — sidebar logic, FAB/bottom sheet, quotes, session polling, end session
5. **Puppet tab restyle** — speech rate slider, volume removed, themed sections
6. **Dashboard restyle** — status banner, gradient cards, collapsible sections
7. **Logs restyle** — segmented controls, themed badges
8. **Config reorganise** — grouped sub-panels, LED section, log_level dropdown
