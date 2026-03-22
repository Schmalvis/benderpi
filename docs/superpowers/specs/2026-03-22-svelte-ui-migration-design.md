# Svelte UI Migration — Design Spec

**Date:** 2026-03-22
**Status:** Approved
**Scope:** Rewrite the BenderPi web UI from vanilla JS to Svelte + Tailwind, preserving the Futurama theme and all functionality.

---

## Problem Statement

The current web UI is ~191 KB of vanilla JavaScript across 6 files, with a monolithic 2,477-line CSS file and 97 implicit globals. There is no build system, no module system, and no component model. As the project grows (camera feeds, motor controls, more config panels), adding features becomes increasingly painful. The UI needs a framework that scales.

---

## Constraints

- No changes to the FastAPI backend — all 32 API endpoints stay as-is
- The Futurama theme (scan lines, glows, dark/light modes, Bender images) must carry forward
- Built output is committed to git — no Node.js required on the Pi
- Auto-deploy via git pull continues to work unchanged
- Real Bender image assets used throughout — no emoji/generic placeholders

---

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Framework | Svelte | Compiles away (near-zero runtime), tiny bundle, simple reactive model. Pi-friendly. |
| CSS | Tailwind + CSS variables | Utility-first for layout, CSS custom properties for Futurama theme tokens. |
| Build tool | Vite | Standard Svelte toolchain, fast builds, good dev experience. |
| Routing | Simple reactive variable | 5 pages, no deep linking needed. No router library. |
| State management | Svelte stores | Built-in, lightweight, no extra library. |
| Deployment | Build locally, commit `web/dist/` | Preserves auto-deploy, no Node.js on Pi. |
| Migration | Full rewrite, not incremental | 97 globals and monolithic CSS make incremental migration impractical. |

---

## 1. Project Structure

```
bender/
├── web/                          ← NEW: Svelte project
│   ├── src/
│   │   ├── App.svelte           ← root: sidebar, nav, auth gate, page router
│   │   ├── main.js              ← Vite entry point
│   │   ├── app.css              ← Tailwind base + Futurama global styles (scan lines, overlays)
│   │   ├── lib/
│   │   │   ├── api.js           ← centralised API client (all 32 endpoints)
│   │   │   ├── stores/
│   │   │   │   ├── health.js    ← polls /api/health every 5s
│   │   │   │   ├── config.js    ← bender_config state + dirty tracking
│   │   │   │   ├── session.js   ← auth PIN + logged-in state
│   │   │   │   └── timers.js    ← active timers, polls every 2s when visible
│   │   │   └── components/      ← reusable shared components
│   │   │       ├── Sidebar.svelte
│   │   │       ├── VolumeSlider.svelte
│   │   │       ├── StatusBadge.svelte
│   │   │       ├── ClipButton.svelte
│   │   │       ├── TimerCard.svelte
│   │   │       └── MetricChart.svelte
│   │   └── pages/               ← one per tab
│   │       ├── Dashboard.svelte
│   │       ├── Puppet.svelte
│   │       ├── Config.svelte
│   │       ├── Logs.svelte
│   │       └── Remote.svelte
│   ├── public/
│   │   ├── assets/              ← Bender images (copied as-is to dist/)
│   │   │   ├── icons8-futurama-bender.svg
│   │   │   ├── bender.png
│   │   │   ├── Futurama-Bender.webp
│   │   │   ├── Bender_Rodriguez.png
│   │   │   ├── bender-cigar.png
│   │   │   └── bender-looking-down.png
│   │   └── favicon.png          ← generated from bender.png
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── dist/                    ← built output (committed to git)
├── scripts/web/
│   ├── app.py                   ← FastAPI — static mount updated to web/dist/
│   ├── auth.py                  ← unchanged
│   ├── static/                  ← OLD vanilla JS (kept briefly for rollback, then removed)
│   └── assets/                  ← OLD assets location (migrated to web/public/assets/)
├── assets/                      ← source Bender images (stay, web/public/ copies from here)
```

---

## 2. Tailwind Configuration

CSS custom properties remain the source of truth. Tailwind wraps them as utility classes.

```js
// tailwind.config.js
export default {
  content: ['./src/**/*.{svelte,js}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        'bg-card': 'var(--bg-card)',
        'bg-sidebar': 'var(--bg-sidebar)',
        'bg-input': 'var(--bg-input)',
        accent: 'var(--accent)',
        'accent-red': 'var(--accent-red)',
        'text-default': 'var(--text)',
        'text-muted': 'var(--text-muted)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        error: 'var(--error)',
        border: 'var(--border)',
      },
      borderRadius: {
        DEFAULT: 'var(--radius)',
        lg: 'var(--radius-lg)',
      },
      boxShadow: {
        DEFAULT: 'var(--shadow)',
        lg: 'var(--shadow-lg)',
        glow: '0 0 12px var(--glow)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      animation: {
        'avatar-glow': 'avatar-glow 3s ease-in-out infinite',
        'status-pulse': 'status-pulse 2s ease-in-out infinite',
      },
      keyframes: {
        'avatar-glow': {
          '0%, 100%': { boxShadow: '0 0 8px var(--glow)' },
          '50%': { boxShadow: '0 0 20px var(--glow), 0 0 40px rgba(74,158,255,0.08)' },
        },
        'status-pulse': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.6' },
        },
      },
    },
  },
}
```

### Theme layers

| Layer | Location | What it contains |
|---|---|---|
| CSS custom properties | `app.css` `:root` block | All 38 theme tokens (--bg, --accent, --glow, etc.) |
| Light theme overrides | `app.css` `[data-theme="light"]` | Token overrides for light mode |
| Global effects | `app.css` | Scan line overlay, body-level repeating gradient |
| Tailwind utilities | `tailwind.config.js` | Theme tokens mapped to utility classes |
| Component-specific | Scoped `<style>` in `.svelte` | Animations, complex layouts unique to one component |

---

## 3. Bender Image Assets

All existing images migrate to `web/public/assets/`. Each has a designated role:

| Image | Role | Location in UI |
|---|---|---|
| `icons8-futurama-bender.svg` | Sidebar logo / avatar | Sidebar header, with glow animation |
| `bender.png` | Favicon + PWA icon | `<link rel="icon">`, generated sizes if needed |
| `Futurama-Bender.webp` | Login / splash screen | PIN entry page background |
| `Bender_Rodriguez.png` | Dashboard hero | Dashboard greeting area |
| `bender-cigar.png` | Idle / waiting state | Puppet mode idle, thinking indicator |
| `bender-looking-down.png` | Error / offline state | Connection lost, service down screens |

---

## 4. App Shell (App.svelte)

The root component owns the layout and page routing.

### Layout

```
┌──────────────────────────────────────────┐
│ Sidebar (220px)  │  Main Content (flex)  │
│                  │                       │
│ [Bender logo]    │  [Page Title]         │
│ Dashboard ●      │  [Page Content]       │
│ Puppet           │                       │
│ Config           │                       │
│ Logs             │                       │
│ Remote           │                       │
│                  │                       │
│ ─── Quick ───    │                       │
│ Volume [====]    │                       │
│ [Silent] [LEDs]  │                       │
└──────────────────────────────────────────┘
```

### Routing

```svelte
<script>
  let currentPage = 'dashboard';
</script>

{#if !$session.authenticated}
  <LoginPage />
{:else}
  <div class="app-shell">
    <Sidebar bind:currentPage />
    <main>
      {#if currentPage === 'dashboard'}
        <Dashboard />
      {:else if currentPage === 'puppet'}
        <Puppet />
      {:else if currentPage === 'config'}
        <Config />
      {:else if currentPage === 'logs'}
        <Logs />
      {:else if currentPage === 'remote'}
        <Remote />
      {/if}
    </main>
  </div>
{/if}
```

### Mobile layout

On narrow screens (<768px), the sidebar collapses to a bottom tab bar with icons only. The quick controls move to a pull-up drawer accessible from the tab bar.

---

## 5. Svelte Stores

### `session.js` — Auth state

```js
import { writable } from 'svelte/store';

export const session = writable({
  authenticated: false,
  pin: null,
});
```

### `health.js` — Live system status

```js
import { readable } from 'svelte/store';
import { getHealth } from '../api.js';

export const health = readable({ status: 'unknown' }, (set) => {
  const poll = async () => {
    try {
      const data = await getHealth();
      set({ ...data, status: 'online' });
    } catch {
      set({ status: 'offline' });
    }
  };
  poll();
  const interval = setInterval(poll, 5000);
  return () => clearInterval(interval);
});
```

### `config.js` — Config state with dirty tracking

```js
import { writable, derived } from 'svelte/store';

export const config = writable({});
export const savedConfig = writable({});
export const isDirty = derived(
  [config, savedConfig],
  ([$config, $saved]) => JSON.stringify($config) !== JSON.stringify($saved)
);
```

### `timers.js` — Active timers

```js
import { writable } from 'svelte/store';
import { getTimers } from '../api.js';

export const timers = writable([]);

export function startTimerPolling() {
  const interval = setInterval(async () => {
    timers.set(await getTimers());
  }, 2000);
  return () => clearInterval(interval);
}
```

---

## 6. API Client (`api.js`)

Single module wrapping all 32 backend endpoints. Replaces scattered `fetch()` calls.

```js
import { get } from 'svelte/store';
import { session } from './stores/session.js';

const BASE = '';  // same origin

async function request(path, options = {}) {
  const { pin } = get(session);
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(pin ? { 'X-Pin': pin } : {}),
      ...options.headers,
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// Health
export const getHealth = () => request('/api/health');

// Actions
export const restartService = () => request('/api/actions/restart', { method: 'POST' });
export const refreshBriefings = () => request('/api/actions/refresh-briefings', { method: 'POST' });
export const rebuildResponses = () => request('/api/actions/rebuild-responses', { method: 'POST' });

// Config
export const getConfig = () => request('/api/config');
export const updateConfig = (data) => request('/api/config', { method: 'PUT', body: JSON.stringify(data) });

// Puppet
export const getClips = () => request('/api/puppet/clips');
export const playClip = (path) => request('/api/puppet/clip', { method: 'POST', body: JSON.stringify({ path }) });
export const speak = (text) => request('/api/puppet/speak', { method: 'POST', body: JSON.stringify({ text }) });
export const setVolume = (vol) => request('/api/puppet/volume', { method: 'POST', body: JSON.stringify({ volume: vol }) });

// Timers
export const getTimers = () => request('/api/timers');
export const cancelTimer = (id) => request(`/api/timers/${id}`, { method: 'DELETE' });

// Logs
export const getConversationLogs = (date) => request(`/api/logs/conversations?date=${date}`);
export const getSystemLog = (lines) => request(`/api/logs/system?lines=${lines}`);
export const getMetrics = () => request('/api/logs/metrics');

// ... remaining endpoints follow the same pattern
```

---

## 7. Page Summaries

### Dashboard.svelte
- Status cards (service state, STT engine, AI backend, CPU temp)
- Active timers list with cancel buttons
- Recent conversations feed (last 20 turns)
- Watchdog alerts panel
- Bender_Rodriguez.png as hero/greeting image

### Puppet.svelte
- TTS text input with speak button
- Soundboard grid grouped by category (uses clip_categories.json)
- Favourites row pinned at top
- Volume slider
- bender-cigar.png for idle state

### Config.svelte
- Sections: General, Audio, STT, TTS, AI Backend, Home Assistant, LED, Watchdog
- AI Backend section: backend mode dropdown, per-scenario routing dropdowns, local LLM model/URL/timeout
- JSON editor fallback for advanced users
- Save/reset buttons with dirty state indicator

### Logs.svelte
- Date picker for conversation log browser
- Session/turn expandable tree view
- System log viewer (tail mode)
- Metrics explorer with basic charting
- CSV export buttons

### Remote.svelte
- Audio file upload for Bender to speak
- Direct AI query input (bypass voice)
- Response display with audio playback

---

## 8. Migration Strategy

### Build order

| Step | Component | Depends on |
|---|---|---|
| 1 | Vite + Svelte + Tailwind scaffolding | — |
| 2 | App shell (Sidebar, nav, auth gate) | Step 1 |
| 3 | API client + stores | Step 1 |
| 4 | Puppet page | Steps 2, 3 |
| 5 | Dashboard page | Steps 2, 3 |
| 6 | Config page | Steps 2, 3 |
| 7 | Logs page | Steps 2, 3 |
| 8 | Remote page | Steps 2, 3 |
| 9 | Mobile responsive pass | Steps 2–8 |
| 10 | Cutover: swap FastAPI static mount | Step 9 |
| 11 | Remove old vanilla JS files | Step 10 |

### Cutover

Single commit changes the FastAPI static mount from `scripts/web/static/` to `web/dist/`. Old files remain in repo for one release cycle (rollback safety), then removed.

### Build workflow

```bash
cd web
npm install          # first time only
npm run build        # outputs to web/dist/
git add web/dist
git commit -m "build: update web UI dist"
git push             # Pi auto-pulls, serves new UI
```

---

## 9. Backend Changes

Minimal — only the static file serving path changes.

### `scripts/web/app.py`

```python
# Before
app.mount("/", StaticFiles(directory=os.path.join(_BASE_DIR, "scripts", "web", "static"), html=True))

# After
app.mount("/", StaticFiles(directory=os.path.join(_BASE_DIR, "web", "dist"), html=True))
```

All API routes unchanged. Auth middleware unchanged. No new endpoints needed.

---

## 10. Files Changed Summary

### New files
| File | Purpose |
|---|---|
| `web/` directory | Entire Svelte project (src, config, build output) |
| `web/public/assets/*.{svg,png,webp}` | Bender images copied from `assets/` |
| `web/public/favicon.png` | Bender favicon |

### Modified files
| File | Changes |
|---|---|
| `scripts/web/app.py` | Static mount path: `scripts/web/static/` → `web/dist/` |
| `.gitignore` | Add `web/node_modules/`, `.superpowers/` |

### Removed files (after cutover)
| File | Why |
|---|---|
| `scripts/web/static/*.js` (6 files) | Replaced by Svelte components |
| `scripts/web/static/style.css` | Replaced by Tailwind + scoped styles |
| `scripts/web/static/index.html` | Replaced by Vite-generated index.html |
| `scripts/web/static/favicon.svg` | Replaced by Bender PNG favicon |

### Unchanged files
| File | Why unchanged |
|---|---|
| `scripts/web/auth.py` | Auth middleware works with any frontend |
| All API endpoints in `app.py` | Frontend-agnostic REST API |
| `speech/clip_categories.json` | Read by backend, served via API |
| `speech/clip_labels.json` | Read by backend, served via API |
| `bender_config.json` | Read/written by backend, served via API |
