# Svelte UI Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the BenderPi web UI from vanilla JS to Svelte + Tailwind, preserving the Futurama theme and all functionality.

**Architecture:** Svelte app in `web/` directory, built with Vite, output committed to `web/dist/`. FastAPI serves `web/dist/` instead of `scripts/web/static/`. CSS custom properties remain the theme source of truth, wrapped by Tailwind utilities.

**Tech Stack:** Svelte 4, Vite 5, Tailwind CSS 3, PostCSS

**Spec:** `docs/superpowers/specs/2026-03-22-svelte-ui-migration-design.md`

---

## File Structure

### New files (web/ directory)

| File | Responsibility |
|---|---|
| `web/package.json` | Dependencies: svelte, vite, tailwindcss, postcss, autoprefixer |
| `web/vite.config.js` | Svelte plugin, build output to `web/dist/` |
| `web/tailwind.config.js` | Futurama theme tokens as Tailwind extensions |
| `web/postcss.config.js` | Tailwind + autoprefixer |
| `web/src/main.js` | Vite entry point, mounts App.svelte |
| `web/src/app.css` | Tailwind directives + Futurama global styles (scan lines, CSS variables) |
| `web/src/App.svelte` | Root: auth gate, sidebar, page router |
| `web/src/lib/api.js` | Centralised API client (all backend endpoints) |
| `web/src/lib/stores/session.js` | Auth PIN state |
| `web/src/lib/stores/health.js` | Polls /api/health every 5s |
| `web/src/lib/stores/config.js` | Config state + dirty tracking |
| `web/src/lib/stores/timers.js` | Active timers polling |
| `web/src/lib/components/Sidebar.svelte` | Nav + quick controls |
| `web/src/lib/components/VolumeSlider.svelte` | Reusable volume control |
| `web/src/lib/components/StatusBadge.svelte` | Online/offline indicator |
| `web/src/lib/components/ClipButton.svelte` | Soundboard clip button with favourite star |
| `web/src/lib/components/TimerCard.svelte` | Timer display with cancel/dismiss |
| `web/src/lib/components/MetricChart.svelte` | Simple bar/line chart for metrics explorer |
| `web/src/pages/Login.svelte` | PIN entry with Bender splash image |
| `web/src/pages/Dashboard.svelte` | Health, metrics, recent conversations, timers |
| `web/src/pages/Puppet.svelte` | TTS input, soundboard grid, favourites |
| `web/src/pages/Config.svelte` | Config editor with sections + AI backend controls |
| `web/src/pages/Logs.svelte` | Conversation browser, system log, metrics, CSV export |
| `web/src/pages/Remote.svelte` | Audio upload, direct AI query |
| `web/public/favicon.png` | Bender favicon (copied from scripts/web/assets/bender.png) |
| `web/public/assets/` | All Bender images copied from assets/ and scripts/web/assets/ |

### Modified files

| File | Change |
|---|---|
| `scripts/web/app.py:787-790` | Remove `/assets` mount, change `/` mount to `web/dist/` |
| `.gitignore` | Add `web/node_modules/` |

### Removed files (after cutover, separate cleanup task)

| File | Replaced by |
|---|---|
| `scripts/web/static/index.html` | Vite-generated `web/dist/index.html` |
| `scripts/web/static/app.js` | `App.svelte` + `api.js` + stores |
| `scripts/web/static/puppet.js` | `pages/Puppet.svelte` |
| `scripts/web/static/dashboard.js` | `pages/Dashboard.svelte` |
| `scripts/web/static/config.js` | `pages/Config.svelte` |
| `scripts/web/static/logs.js` | `pages/Logs.svelte` |
| `scripts/web/static/remote.js` | `pages/Remote.svelte` |
| `scripts/web/static/style.css` | `app.css` + Tailwind + scoped styles |
| `scripts/web/static/favicon.svg` | `web/public/favicon.png` |

---

## Task 1: Scaffold Vite + Svelte + Tailwind Project

**Files:**
- Create: `web/package.json`
- Create: `web/vite.config.js`
- Create: `web/tailwind.config.js`
- Create: `web/postcss.config.js`
- Create: `web/src/main.js`
- Create: `web/src/app.css`
- Create: `web/src/App.svelte` (minimal placeholder)
- Modify: `.gitignore`

- [ ] **Step 1: Create web directory and initialise npm project**

```bash
cd c:/ws/benderpi
mkdir -p web/src web/public/assets
cd web
npm init -y
```

- [ ] **Step 2: Install dependencies**

```bash
cd c:/ws/benderpi/web
npm install --save-dev svelte @sveltejs/vite-plugin-svelte vite tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

- [ ] **Step 3: Write vite.config.js**

Create `web/vite.config.js`:

```js
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
```

- [ ] **Step 4: Write tailwind.config.js with Futurama theme tokens**

Overwrite `web/tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{svelte,js}', './index.html'],
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
        'timer-flash': 'timer-flash 0.8s ease-in-out infinite',
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
        'timer-flash': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.3' },
        },
      },
    },
  },
  plugins: [],
};
```

- [ ] **Step 5: Write app.css with Tailwind directives and Futurama globals**

Create `web/src/app.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* ── Futurama Theme Tokens (Dark — default) ── */
:root {
  --bg: #0a0e1a;
  --bg-card: #12162a;
  --bg-sidebar: #0d1020;
  --bg-input: #1a1e30;
  --accent: #4a9eff;
  --accent-red: #e94560;
  --text: #e0e0e0;
  --text-muted: #6a6a9a;
  --success: #4ecca3;
  --warning: #f0a500;
  --error: #e94560;
  --border: #2a2a4a;
  --glow: rgba(74,158,255,0.15);
  --scanline: rgba(0,200,255,0.015);
  --radius: 8px;
  --radius-lg: 12px;
  --shadow: 0 2px 12px rgba(0, 0, 0, 0.4);
  --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.6);
  --transition: 0.2s ease;
  --font-mono: "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

/* ── Light Theme ── */
[data-theme="light"] {
  --bg: #f0f2f5;
  --bg-card: #fff;
  --bg-sidebar: #e8eaf0;
  --bg-input: #f5f5f5;
  --accent: #2a7cdb;
  --accent-red: #d63447;
  --text: #1a1a2e;
  --text-muted: #666680;
  --success: #2d8f6f;
  --warning: #c48200;
  --error: #d63447;
  --border: #d0d0e0;
  --glow: rgba(42,124,219,0.08);
  --scanline: none;
  --shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
  --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.15);
}

/* ── Global Reset ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 16px; -webkit-text-size-adjust: 100%; }
body {
  font-family: var(--font-sans);
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  min-height: 100vh;
  transition: background var(--transition), color var(--transition);
}

/* ── Scan Line Overlay ── */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background: repeating-linear-gradient(
    0deg,
    var(--scanline) 0px,
    var(--scanline) 1px,
    transparent 1px,
    transparent 3px
  );
  pointer-events: none;
  z-index: 9999;
}
```

- [ ] **Step 6: Write minimal App.svelte placeholder**

Create `web/src/App.svelte`:

```svelte
<script>
  let currentPage = 'dashboard';
</script>

<div class="min-h-screen bg-bg text-text-default font-sans">
  <h1 class="text-accent text-2xl p-4">BenderPi — Svelte Migration</h1>
  <p class="text-text-muted px-4">Scaffold working. Current page: {currentPage}</p>
</div>
```

- [ ] **Step 7: Write main.js entry point**

Create `web/src/main.js`:

```js
import './app.css';
import App from './App.svelte';

const app = new App({
  target: document.getElementById('app'),
});

export default app;
```

- [ ] **Step 8: Write index.html**

Create `web/index.html`:

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>BenderPi</title>
  <link rel="icon" type="image/png" href="/favicon.png">
</head>
<body>
  <div id="app"></div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
```

- [ ] **Step 9: Copy Bender assets to web/public/**

```bash
cp c:/ws/benderpi/assets/icons8-futurama-bender.svg c:/ws/benderpi/web/public/assets/
cp c:/ws/benderpi/assets/Futurama-Bender.webp c:/ws/benderpi/web/public/assets/
cp c:/ws/benderpi/assets/Bender_Rodriguez.png c:/ws/benderpi/web/public/assets/
cp c:/ws/benderpi/assets/bender-cigar.png c:/ws/benderpi/web/public/assets/
cp c:/ws/benderpi/assets/bender-looking-down.png c:/ws/benderpi/web/public/assets/
cp c:/ws/benderpi/scripts/web/assets/bender.png c:/ws/benderpi/web/public/favicon.png
cp c:/ws/benderpi/scripts/web/assets/bender.png c:/ws/benderpi/web/public/assets/bender.png
```

- [ ] **Step 10: Update .gitignore**

Add to `.gitignore`:

```
web/node_modules/
.superpowers/
```

- [ ] **Step 11: Verify build works**

```bash
cd c:/ws/benderpi/web
npm run dev
```

Expected: Vite dev server starts, browser shows "BenderPi — Svelte Migration" with accent blue text on dark background, scan lines visible.

Press Ctrl+C to stop.

```bash
npm run build
```

Expected: `web/dist/` directory created with `index.html`, `assets/` containing JS/CSS bundles.

- [ ] **Step 12: Commit scaffold**

```bash
cd c:/ws/benderpi
git add web/ .gitignore
git commit -m "feat(web): scaffold Svelte + Tailwind project with Futurama theme"
```

---

## Task 2: API Client + Stores

**Files:**
- Create: `web/src/lib/api.js`
- Create: `web/src/lib/stores/session.js`
- Create: `web/src/lib/stores/health.js`
- Create: `web/src/lib/stores/config.js`
- Create: `web/src/lib/stores/timers.js`

- [ ] **Step 1: Write session store**

Create `web/src/lib/stores/session.js`:

```js
import { writable } from 'svelte/store';

const PIN_KEY = 'benderpi_pin';

function createSession() {
  const stored = sessionStorage.getItem(PIN_KEY);
  const { subscribe, set, update } = writable({
    authenticated: false,
    pin: stored,
  });

  return {
    subscribe,
    login(pin) {
      sessionStorage.setItem(PIN_KEY, pin);
      set({ authenticated: true, pin });
    },
    logout() {
      sessionStorage.removeItem(PIN_KEY);
      set({ authenticated: false, pin: null });
    },
    restore(pin) {
      set({ authenticated: true, pin });
    },
  };
}

export const session = createSession();
```

- [ ] **Step 2: Write API client**

Create `web/src/lib/api.js`:

```js
import { get } from 'svelte/store';
import { session } from './stores/session.js';

async function request(path, options = {}) {
  const { pin } = get(session);
  const headers = { ...options.headers };
  if (pin) headers['X-Bender-Pin'] = pin;
  if (options.body && typeof options.body === 'string') {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(path, { ...options, headers });

  if (res.status === 401) {
    session.logout();
    throw new Error('Unauthorized');
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// Health
export const getHealth = () => request('/api/health');
export const getSessionStatus = () => request('/api/actions/session-status');
export const getServiceStatus = () => request('/api/actions/service-status');

// Actions
export const endSession = () => request('/api/actions/end-session', { method: 'POST' });
export const restartService = () => request('/api/actions/restart', { method: 'POST' });
export const refreshBriefings = () => request('/api/actions/refresh-briefings', { method: 'POST' });
export const rebuildResponses = () => request('/api/actions/prebuild', { method: 'POST' });
export const generateStatus = () => request('/api/actions/generate-status', { method: 'POST' });
export const toggleMode = (mode) => request('/api/actions/toggle-mode', { method: 'POST', body: JSON.stringify({ mode }) });

// Config
export const getConfig = () => request('/api/config');
export const updateConfig = (data) => request('/api/config', { method: 'PUT', body: JSON.stringify(data) });
export const getWatchdogConfig = () => request('/api/config/watchdog');
export const updateWatchdogConfig = (data) => request('/api/config/watchdog', { method: 'PUT', body: JSON.stringify(data) });
export const getVolume = () => request('/api/config/volume');
export const setVolume = (volume) => request('/api/config/volume', { method: 'POST', body: JSON.stringify({ volume }) });

// Puppet
export const speak = (text) => request('/api/puppet/speak', { method: 'POST', body: JSON.stringify({ text }) });
export const playClip = (path) => request('/api/puppet/clip', { method: 'POST', body: JSON.stringify({ path }) });
export const getClips = () => request('/api/puppet/clips');
export const setFavourite = (path, favourite) => request('/api/puppet/favourite', { method: 'POST', body: JSON.stringify({ path, favourite }) });

// Status
export const getStatus = () => request('/api/status');
export const refreshStatus = () => request('/api/status/refresh', { method: 'POST' });

// Logs
export const getConversationDates = () => request('/api/logs/conversations');
export const getConversationLog = (date) => request(`/api/logs/conversations/${date}`);
export const getSystemLog = (lines = 100) => request(`/api/logs/system?lines=${lines}`);
export const getMetrics = () => request('/api/logs/metrics');
export const downloadLog = (filename) => `/api/logs/download/${filename}`;

// Timers
export const getTimers = () => request('/api/timers');
export const createTimer = (data) => request('/api/timers', { method: 'POST', body: JSON.stringify(data) });
export const cancelTimer = (id) => request(`/api/timers/${id}`, { method: 'DELETE' });
export const dismissTimer = (id) => request(`/api/timers/${id}/dismiss`, { method: 'POST' });
export const dismissAllTimers = () => request('/api/timers/dismiss-all', { method: 'POST' });

// Remote
export const remoteAsk = (text) => request('/api/remote/ask', { method: 'POST', body: JSON.stringify({ text }) });
```

- [ ] **Step 3: Write health store**

Create `web/src/lib/stores/health.js`:

```js
import { readable } from 'svelte/store';
import { getHealth } from '../api.js';

export const health = readable({ status: 'unknown' }, (set) => {
  let active = true;

  const poll = async () => {
    if (!active) return;
    try {
      const data = await getHealth();
      set({ ...data, status: 'online' });
    } catch {
      set({ status: 'offline' });
    }
  };

  poll();
  const interval = setInterval(poll, 5000);
  return () => { active = false; clearInterval(interval); };
});
```

- [ ] **Step 4: Write config store**

Create `web/src/lib/stores/config.js`:

```js
import { writable, derived, get } from 'svelte/store';
import { getConfig, updateConfig as apiUpdateConfig } from '../api.js';

export const config = writable({});
export const savedConfig = writable({});
export const isDirty = derived(
  [config, savedConfig],
  ([$config, $saved]) => JSON.stringify($config) !== JSON.stringify($saved)
);

export async function loadConfig() {
  const data = await getConfig();
  config.set(data);
  savedConfig.set(structuredClone(data));
}

export async function saveConfig() {
  const data = get(config);
  await apiUpdateConfig(data);
  savedConfig.set(structuredClone(data));
}

export function resetConfig() {
  const saved = get(savedConfig);
  config.set(structuredClone(saved));
}
```

- [ ] **Step 5: Write timers store**

Create `web/src/lib/stores/timers.js`:

```js
import { writable } from 'svelte/store';
import { getTimers } from '../api.js';

export const timers = writable([]);

export function startTimerPolling() {
  const interval = setInterval(async () => {
    try {
      const data = await getTimers();
      timers.set(data.timers || []);
    } catch {
      // Leave existing timer state intact on failure
    }
  }, 2000);
  return () => clearInterval(interval);
}
```

- [ ] **Step 6: Verify imports resolve**

```bash
cd c:/ws/benderpi/web
npm run build
```

Expected: Build succeeds (stores won't be used yet but imports should resolve).

- [ ] **Step 7: Commit stores and API client**

```bash
cd c:/ws/benderpi
git add web/src/lib/
git commit -m "feat(web): add API client and Svelte stores (session, health, config, timers)"
```

---

## Task 3: Login Page + Auth Gate

**Files:**
- Create: `web/src/pages/Login.svelte`
- Modify: `web/src/App.svelte`

**Reference:** Current auth flow in `scripts/web/static/app.js:238-260` — tests PIN by calling `/api/actions/service-status`, stores in `sessionStorage`.

- [ ] **Step 1: Write Login.svelte**

Create `web/src/pages/Login.svelte`:

```svelte
<script>
  import { session } from '../lib/stores/session.js';
  import { getServiceStatus } from '../lib/api.js';

  let pin = '';
  let error = '';
  let loading = false;

  async function handleLogin() {
    if (!pin.trim()) return;
    loading = true;
    error = '';

    // Temporarily set PIN so api.js can use it
    sessionStorage.setItem('benderpi_pin', pin);
    session.restore(pin);

    try {
      await getServiceStatus();
      session.login(pin);
    } catch (e) {
      error = 'Invalid PIN. Try again, meatbag.';
      session.logout();
    } finally {
      loading = false;
    }
  }

  function handleKeydown(e) {
    if (e.key === 'Enter') handleLogin();
  }
</script>

<div class="min-h-screen flex items-center justify-center bg-bg">
  <div class="bg-bg-card border border-border rounded-lg shadow-lg p-8 w-full max-w-sm text-center">
    <img
      src="/assets/Futurama-Bender.webp"
      alt="Bender"
      class="w-32 h-32 mx-auto mb-6 object-contain"
    />
    <h1 class="text-accent text-2xl font-bold mb-2">BenderPi</h1>
    <p class="text-text-muted text-sm mb-6">Enter PIN to continue</p>

    <input
      type="password"
      inputmode="numeric"
      pattern="[0-9]*"
      maxlength="16"
      bind:value={pin}
      on:keydown={handleKeydown}
      placeholder="PIN"
      class="w-full bg-bg-input border border-border rounded px-4 py-3 text-text-default
             text-center text-lg tracking-widest focus:outline-none focus:border-accent
             transition-colors"
      disabled={loading}
    />

    {#if error}
      <p class="text-error text-sm mt-3">{error}</p>
    {/if}

    <button
      on:click={handleLogin}
      disabled={loading || !pin.trim()}
      class="mt-4 w-full bg-accent text-bg font-bold py-3 rounded
             hover:opacity-90 transition-opacity disabled:opacity-50"
    >
      {loading ? 'Authenticating...' : 'Bite my shiny metal app'}
    </button>
  </div>
</div>
```

- [ ] **Step 2: Update App.svelte with auth gate**

Overwrite `web/src/App.svelte`:

```svelte
<script>
  import { onMount } from 'svelte';
  import { session } from './lib/stores/session.js';
  import { getServiceStatus } from './lib/api.js';
  import Login from './pages/Login.svelte';

  let checking = true;

  onMount(async () => {
    const { pin } = $session;
    if (pin) {
      try {
        session.restore(pin);
        await getServiceStatus();
        session.login(pin);
      } catch {
        session.logout();
      }
    }
    checking = false;
  });
</script>

{#if checking}
  <div class="min-h-screen flex items-center justify-center bg-bg">
    <img src="/assets/icons8-futurama-bender.svg" alt="Loading" class="w-16 h-16 animate-pulse" />
  </div>
{:else if !$session.authenticated}
  <Login />
{:else}
  <div class="min-h-screen bg-bg text-text-default font-sans">
    <p class="p-4 text-accent">Authenticated! Shell coming in next task.</p>
  </div>
{/if}
```

- [ ] **Step 3: Build and verify**

```bash
cd c:/ws/benderpi/web && npm run build
```

Expected: Build succeeds. `web/dist/` contains the login page.

- [ ] **Step 4: Commit**

```bash
cd c:/ws/benderpi
git add web/src/
git commit -m "feat(web): add Login page with Bender splash and PIN auth"
```

---

## Task 4: App Shell + Sidebar

**Files:**
- Create: `web/src/lib/components/Sidebar.svelte`
- Create: `web/src/lib/components/VolumeSlider.svelte`
- Modify: `web/src/App.svelte`

**Reference:** Current sidebar in `scripts/web/static/index.html:40-159` and `scripts/web/static/app.js:262-400`.

- [ ] **Step 1: Write VolumeSlider.svelte**

Create `web/src/lib/components/VolumeSlider.svelte`:

```svelte
<script>
  import { getVolume, setVolume } from '../api.js';
  import { onMount } from 'svelte';

  let volume = 80;
  let inflight = false;

  onMount(async () => {
    try {
      const data = await getVolume();
      volume = data.volume ?? 80;
    } catch { /* use default */ }
  });

  async function handleChange() {
    if (inflight) return;
    inflight = true;
    try {
      await setVolume(volume);
    } catch { /* ignore */ }
    inflight = false;
  }
</script>

<div class="text-xs text-text-muted mb-1">Volume</div>
<input
  type="range"
  min="0"
  max="100"
  bind:value={volume}
  on:change={handleChange}
  on:input={handleChange}
  class="w-full h-1 bg-bg-input rounded appearance-none cursor-pointer
         [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3
         [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-accent
         [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:shadow-glow"
/>
<div class="text-xs text-text-muted text-right">{volume}%</div>
```

- [ ] **Step 2: Write Sidebar.svelte**

Create `web/src/lib/components/Sidebar.svelte`:

```svelte
<script>
  import { health } from '../stores/health.js';
  import { session } from '../stores/session.js';
  import { endSession } from '../api.js';
  import VolumeSlider from './VolumeSlider.svelte';

  export let currentPage = 'dashboard';

  const pages = [
    { id: 'dashboard', label: 'Dashboard', icon: '📊' },
    { id: 'puppet', label: 'Puppet', icon: '🎭' },
    { id: 'config', label: 'Config', icon: '⚙️' },
    { id: 'logs', label: 'Logs', icon: '📋' },
    { id: 'remote', label: 'Remote', icon: '🎤' },
  ];

  async function handleEndSession() {
    try { await endSession(); } catch { /* ignore */ }
  }
</script>

<aside class="w-56 bg-bg-sidebar border-r border-border flex flex-col p-4 shrink-0">
  <!-- Logo -->
  <div class="flex items-center gap-3 mb-6 pb-4 border-b border-border">
    <img
      src="/assets/icons8-futurama-bender.svg"
      alt="Bender"
      class="w-10 h-10 rounded-full border-2 border-accent animate-avatar-glow"
    />
    <div>
      <div class="text-xl font-extrabold text-accent" style="text-shadow: 0 0 12px var(--glow)">
        BenderPi
      </div>
      <div class="text-[10px] text-text-muted uppercase tracking-widest">Voice Assistant</div>
    </div>
  </div>

  <!-- Navigation -->
  <nav class="flex flex-col gap-0.5">
    {#each pages as page}
      <button
        class="flex items-center gap-2 px-3 py-2.5 rounded text-sm transition-all text-left w-full
               {currentPage === page.id
                 ? 'bg-bg-card text-accent shadow-[inset_3px_0_0_var(--accent)]'
                 : 'text-text-muted hover:bg-bg-card hover:text-text-default'}"
        on:click={() => currentPage = page.id}
      >
        <span>{page.icon}</span>
        <span>{page.label}</span>
      </button>
    {/each}
  </nav>

  <!-- Quick Controls -->
  <div class="mt-auto pt-4 border-t border-border">
    <div class="text-[10px] text-text-muted uppercase tracking-widest mb-2">Quick Controls</div>
    <VolumeSlider />
    <div class="flex gap-1.5 mt-2">
      <button
        on:click={handleEndSession}
        class="flex-1 bg-bg-input border border-border rounded px-2 py-1.5
               text-[10px] text-text-muted hover:text-accent hover:border-accent transition-colors"
      >
        End Session
      </button>
      <button
        on:click={() => session.logout()}
        class="flex-1 bg-bg-input border border-border rounded px-2 py-1.5
               text-[10px] text-text-muted hover:text-error hover:border-error transition-colors"
      >
        Logout
      </button>
    </div>
  </div>

  <!-- Status indicator -->
  <div class="mt-3 text-[10px] text-text-muted flex items-center gap-1.5">
    <span class="inline-block w-2 h-2 rounded-full {$health.status === 'online' ? 'bg-success animate-status-pulse' : 'bg-error'}"></span>
    {$health.status === 'online' ? 'Connected' : 'Offline'}
  </div>
</aside>
```

- [ ] **Step 3: Update App.svelte with full shell**

Replace the authenticated block in `web/src/App.svelte`:

```svelte
<script>
  import { onMount } from 'svelte';
  import { session } from './lib/stores/session.js';
  import { getServiceStatus } from './lib/api.js';
  import Login from './pages/Login.svelte';
  import Sidebar from './lib/components/Sidebar.svelte';

  let checking = true;
  let currentPage = 'dashboard';

  onMount(async () => {
    const { pin } = $session;
    if (pin) {
      try {
        session.restore(pin);
        await getServiceStatus();
        session.login(pin);
      } catch {
        session.logout();
      }
    }
    checking = false;
  });
</script>

{#if checking}
  <div class="min-h-screen flex items-center justify-center bg-bg">
    <img src="/assets/icons8-futurama-bender.svg" alt="Loading" class="w-16 h-16 animate-pulse" />
  </div>
{:else if !$session.authenticated}
  <Login />
{:else}
  <div class="min-h-screen flex bg-bg text-text-default font-sans">
    <Sidebar bind:currentPage />
    <main class="flex-1 p-6 overflow-y-auto">
      <div class="text-[11px] text-text-muted uppercase tracking-wider mb-4">
        {currentPage}
      </div>
      <p class="text-text-muted">Page content coming next...</p>
    </main>
  </div>
{/if}
```

- [ ] **Step 4: Build and verify**

```bash
cd c:/ws/benderpi/web && npm run build
```

Expected: Build succeeds. Shell with sidebar renders after auth.

- [ ] **Step 5: Commit**

```bash
cd c:/ws/benderpi
git add web/src/
git commit -m "feat(web): add app shell with Sidebar, VolumeSlider, and page routing"
```

---

## Task 5: Puppet Page

**Files:**
- Create: `web/src/lib/components/ClipButton.svelte`
- Create: `web/src/pages/Puppet.svelte`
- Modify: `web/src/App.svelte` (add import + route)

**Reference:** Current puppet in `scripts/web/static/puppet.js` (355 lines). Features: TTS input, soundboard grid grouped by category, favourites row, clip play/favourite.

- [ ] **Step 1: Write ClipButton.svelte**

Create `web/src/lib/components/ClipButton.svelte`:

```svelte
<script>
  import { playClip, setFavourite } from '../api.js';

  export let clip;
  export let onFavouriteToggle = () => {};

  let playing = false;

  async function handlePlay() {
    playing = true;
    try {
      await playClip(clip.path);
    } catch { /* ignore */ }
    playing = false;
  }

  async function handleStar(e) {
    e.stopPropagation();
    const newState = !clip.favourite;
    try {
      await setFavourite(clip.path, newState);
      clip.favourite = newState;
      onFavouriteToggle();
    } catch { /* ignore */ }
  }
</script>

<button
  on:click={handlePlay}
  class="relative bg-bg-card border border-border rounded px-3 py-2 text-left
         hover:border-accent hover:shadow-glow transition-all group w-full
         {playing ? 'border-accent shadow-glow' : ''}"
  title={clip.label}
>
  <div class="flex items-center gap-2">
    <span class="text-accent text-xs opacity-60 group-hover:opacity-100">▶</span>
    <span class="text-sm text-text-default truncate flex-1">{clip.label}</span>
    <button
      on:click={handleStar}
      class="text-sm hover:scale-125 transition-transform"
      title={clip.favourite ? 'Remove favourite' : 'Add favourite'}
    >
      {clip.favourite ? '★' : '☆'}
    </button>
  </div>
</button>
```

- [ ] **Step 2: Write Puppet.svelte**

Create `web/src/pages/Puppet.svelte`:

```svelte
<script>
  import { onMount } from 'svelte';
  import { getClips, speak } from '../lib/api.js';
  import ClipButton from '../lib/components/ClipButton.svelte';

  let clips = [];
  let ttsText = '';
  let speaking = false;
  let refreshKey = 0;

  $: grouped = groupByCategory(clips);
  $: favourites = clips.filter(c => c.favourite);

  onMount(loadClips);

  async function loadClips() {
    try {
      const data = await getClips();
      clips = data.clips || [];
    } catch { /* ignore */ }
  }

  function groupByCategory(clipList) {
    const groups = {};
    for (const clip of clipList) {
      const cat = clip.category || 'clips';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(clip);
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }

  async function handleSpeak() {
    if (!ttsText.trim() || speaking) return;
    speaking = true;
    try {
      await speak(ttsText);
      ttsText = '';
    } catch { /* ignore */ }
    speaking = false;
  }

  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSpeak();
    }
  }

  function handleFavToggle() {
    refreshKey++;
    clips = [...clips]; // trigger reactivity
  }
</script>

<div class="space-y-6">
  <!-- TTS Input -->
  <div class="bg-bg-card border border-border rounded-lg p-4">
    <div class="flex gap-3">
      <input
        type="text"
        bind:value={ttsText}
        on:keydown={handleKeydown}
        placeholder="Type something for Bender to say..."
        class="flex-1 bg-bg-input border border-border rounded px-4 py-2 text-text-default
               focus:outline-none focus:border-accent transition-colors"
        disabled={speaking}
      />
      <button
        on:click={handleSpeak}
        disabled={speaking || !ttsText.trim()}
        class="bg-accent text-bg font-bold px-6 py-2 rounded
               hover:opacity-90 transition-opacity disabled:opacity-50"
      >
        {speaking ? 'Speaking...' : 'Speak'}
      </button>
    </div>
  </div>

  <!-- Favourites -->
  {#if favourites.length > 0}
    <div>
      <h3 class="text-xs text-text-muted uppercase tracking-wider mb-2">Favourites</h3>
      <div class="flex gap-2 overflow-x-auto pb-2">
        {#each favourites as clip (clip.path)}
          <div class="shrink-0 w-48">
            <ClipButton {clip} onFavouriteToggle={handleFavToggle} />
          </div>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Clip Categories -->
  {#each grouped as [category, categoryClips] (category)}
    <details open class="group">
      <summary class="cursor-pointer text-xs text-text-muted uppercase tracking-wider mb-2
                      flex items-center gap-2 select-none">
        <span class="transition-transform group-open:rotate-90">▶</span>
        {category}
        <span class="bg-bg-input px-2 py-0.5 rounded text-[10px]">{categoryClips.length}</span>
      </summary>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2 mt-2">
        {#each categoryClips as clip (clip.path)}
          <ClipButton {clip} onFavouriteToggle={handleFavToggle} />
        {/each}
      </div>
    </details>
  {/each}
</div>
```

- [ ] **Step 3: Wire Puppet into App.svelte**

In `web/src/App.svelte`, add the import and route:

Add to imports:
```js
import Puppet from './pages/Puppet.svelte';
```

Add to the `{#if currentPage}` block:
```svelte
{#if currentPage === 'puppet'}
  <Puppet />
{:else}
  <p class="text-text-muted">Page content coming next...</p>
{/if}
```

- [ ] **Step 4: Build and verify**

```bash
cd c:/ws/benderpi/web && npm run build
```

Expected: Build succeeds. Puppet page shows TTS input and clip categories.

- [ ] **Step 5: Commit**

```bash
cd c:/ws/benderpi
git add web/src/
git commit -m "feat(web): add Puppet page with soundboard, TTS input, and favourites"
```

---

## Task 6: Dashboard Page

**Files:**
- Create: `web/src/lib/components/StatusBadge.svelte`
- Create: `web/src/lib/components/TimerCard.svelte`
- Create: `web/src/pages/Dashboard.svelte`
- Modify: `web/src/App.svelte` (add import + route)

**Reference:** Current dashboard in `scripts/web/static/dashboard.js` (674 lines). Features: health cards, timer list, recent conversations, watchdog alerts, status refresh.

- [ ] **Step 1: Write StatusBadge.svelte**

Create `web/src/lib/components/StatusBadge.svelte`:

```svelte
<script>
  export let status = 'unknown';
  export let label = '';

  const colors = {
    online: 'bg-success',
    offline: 'bg-error',
    unknown: 'bg-warning',
  };
</script>

<div class="flex items-center gap-2">
  <span class="inline-block w-2 h-2 rounded-full {colors[status] || colors.unknown}
               {status === 'online' ? 'animate-status-pulse' : ''}"></span>
  <span class="text-sm">{label || status}</span>
</div>
```

- [ ] **Step 2: Write TimerCard.svelte**

Create `web/src/lib/components/TimerCard.svelte`:

```svelte
<script>
  import { cancelTimer, dismissTimer } from '../api.js';

  export let timer;
  export let onUpdate = () => {};

  $: remaining = formatRemaining(timer);
  $: firing = timer.state === 'firing';

  function formatRemaining(t) {
    if (t.state === 'firing') return 'FIRING';
    if (!t.remaining_seconds) return '--';
    const m = Math.floor(t.remaining_seconds / 60);
    const s = t.remaining_seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  async function handleCancel() {
    try {
      await cancelTimer(timer.id);
      onUpdate();
    } catch { /* ignore */ }
  }

  async function handleDismiss() {
    try {
      await dismissTimer(timer.id);
      onUpdate();
    } catch { /* ignore */ }
  }
</script>

<div class="bg-bg-card border border-border rounded-lg p-4 flex items-center gap-4
            {firing ? 'border-error animate-timer-flash' : ''}">
  <div class="flex-1">
    <div class="text-sm font-medium text-text-default">{timer.label || 'Timer'}</div>
    <div class="text-2xl font-mono {firing ? 'text-error' : 'text-accent'}">{remaining}</div>
  </div>
  {#if firing}
    <button
      on:click={handleDismiss}
      class="bg-error text-white px-3 py-1.5 rounded text-sm font-bold hover:opacity-90"
    >
      Dismiss
    </button>
  {:else}
    <button
      on:click={handleCancel}
      class="bg-bg-input border border-border text-text-muted px-3 py-1.5 rounded text-sm
             hover:text-error hover:border-error transition-colors"
    >
      Cancel
    </button>
  {/if}
</div>
```

- [ ] **Step 3: Write Dashboard.svelte**

Create `web/src/pages/Dashboard.svelte`:

```svelte
<script>
  import { onMount, onDestroy } from 'svelte';
  import { health } from '../lib/stores/health.js';
  import { timers, startTimerPolling } from '../lib/stores/timers.js';
  import { getStatus, getConversationDates, getConversationLog } from '../lib/api.js';
  import StatusBadge from '../lib/components/StatusBadge.svelte';
  import TimerCard from '../lib/components/TimerCard.svelte';

  let status = {};
  let recentTurns = [];
  let stopPolling;

  onMount(async () => {
    stopPolling = startTimerPolling();
    try {
      status = await getStatus();
    } catch { /* ignore */ }
    await loadRecentConversations();
  });

  onDestroy(() => { if (stopPolling) stopPolling(); });

  async function loadRecentConversations() {
    try {
      const dates = await getConversationDates();
      if (dates.dates && dates.dates.length > 0) {
        const latest = dates.dates[0];
        const log = await getConversationLog(latest);
        recentTurns = (log.entries || [])
          .filter(e => e.type === 'turn')
          .slice(-10)
          .reverse();
      }
    } catch { /* ignore */ }
  }
</script>

<div class="space-y-6">
  <!-- Hero -->
  <div class="bg-bg-card border border-border rounded-lg p-6 flex items-center gap-6">
    <img src="/assets/Bender_Rodriguez.png" alt="Bender" class="w-20 h-20 object-contain" />
    <div>
      <h2 class="text-2xl font-bold text-accent">Good news, everyone!</h2>
      <p class="text-text-muted text-sm mt-1">
        {$health.status === 'online' ? "Bender is online and ready to insult humanity." : "Bender appears to be offline."}
      </p>
    </div>
  </div>

  <!-- Status Cards -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
    <div class="bg-bg-card border border-border rounded-lg p-4">
      <div class="text-[11px] text-text-muted uppercase">Status</div>
      <div class="mt-1"><StatusBadge status={$health.status} label={$health.status === 'online' ? 'Online' : 'Offline'} /></div>
    </div>
    <div class="bg-bg-card border border-border rounded-lg p-4">
      <div class="text-[11px] text-text-muted uppercase">STT Engine</div>
      <div class="text-lg font-semibold text-accent mt-1">{status.stt_engine || '—'}</div>
    </div>
    <div class="bg-bg-card border border-border rounded-lg p-4">
      <div class="text-[11px] text-text-muted uppercase">AI Backend</div>
      <div class="text-lg font-semibold text-accent mt-1">{status.ai_backend || '—'}</div>
    </div>
    <div class="bg-bg-card border border-border rounded-lg p-4">
      <div class="text-[11px] text-text-muted uppercase">CPU Temp</div>
      <div class="text-lg font-semibold text-accent mt-1">{status.cpu_temp || '—'}</div>
    </div>
  </div>

  <!-- Active Timers -->
  {#if $timers.length > 0}
    <div>
      <h3 class="text-xs text-text-muted uppercase tracking-wider mb-2">Active Timers</h3>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        {#each $timers as timer (timer.id)}
          <TimerCard {timer} onUpdate={() => {}} />
        {/each}
      </div>
    </div>
  {/if}

  <!-- Recent Conversations -->
  <div class="bg-bg-card border border-border rounded-lg p-4">
    <h3 class="text-[11px] text-text-muted uppercase tracking-wider mb-3">Recent Conversations</h3>
    {#if recentTurns.length === 0}
      <p class="text-text-muted text-sm flex items-center gap-2">
        <img src="/assets/bender-cigar.png" alt="" class="w-6 h-6 object-contain" />
        No conversations yet today.
      </p>
    {:else}
      <div class="space-y-1 font-mono text-sm">
        {#each recentTurns as turn}
          <div class="text-text-muted">
            <span class="opacity-60">{turn.ts?.split('T')[1]?.slice(0, 5) || ''}</span>
            — "{turn.user_text || ''}"
            → <span class="text-accent">{turn.method || ''}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
```

- [ ] **Step 4: Wire Dashboard into App.svelte**

Add import and route for Dashboard alongside Puppet.

- [ ] **Step 5: Build and verify**

```bash
cd c:/ws/benderpi/web && npm run build
```

- [ ] **Step 6: Commit**

```bash
cd c:/ws/benderpi
git add web/src/
git commit -m "feat(web): add Dashboard page with status cards, timers, and conversations"
```

---

## Task 7: Config Page

**Files:**
- Create: `web/src/pages/Config.svelte`
- Modify: `web/src/App.svelte` (add import + route)

**Reference:** Current config in `scripts/web/static/config.js` (745 lines). Largest page. Sections: general, audio, STT, TTS, AI backend (with routing dropdowns), HA, LED, watchdog.

- [ ] **Step 1: Write Config.svelte**

Create `web/src/pages/Config.svelte`. This is the largest page — implement section by section within the component. Key sections:

- General settings (speech_rate, etc.)
- AI Backend (ai_backend dropdown, per-scenario routing, local_llm_model/url/timeout)
- Home Assistant (ha_url, ha_token, ha_room_synonyms)
- LED configuration (colours, brightness)
- Watchdog thresholds

The component should:
- Load config on mount via `loadConfig()` from the config store
- Bind form fields to `$config` properties
- Show save/reset buttons when `$isDirty` is true
- Call `saveConfig()` on save, `resetConfig()` on reset
- Group sections with collapsible `<details>` elements
- Include the AI routing dropdowns (conversation/knowledge/creative → local_first/local_only/cloud_only)

Implementation follows the same Tailwind patterns as other pages. Due to the page's size (~200-300 lines of Svelte), the implementer should build it iteratively — start with a working skeleton that loads/saves config, then add sections one by one.

- [ ] **Step 2: Wire Config into App.svelte**

- [ ] **Step 3: Build and verify**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(web): add Config page with AI backend, HA, LED, and watchdog sections"
```

---

## Task 8: Logs Page

**Files:**
- Create: `web/src/pages/Logs.svelte`
- Modify: `web/src/App.svelte` (add import + route)

**Reference:** Current logs in `scripts/web/static/logs.js` (534 lines). Features: date picker, conversation browser with expandable sessions/turns, system log viewer, metrics display, CSV download.

- [ ] **Step 1: Write Logs.svelte**

Create `web/src/pages/Logs.svelte`. Key features:

- Date picker (list from `/api/logs/conversations`)
- Conversation log: expandable session/turn tree
- System log: text area showing tail of bender.log
- Metrics summary
- Download buttons (link to `/api/logs/download/{filename}`)

The component should use tabs or sections for conversation/system/metrics views.

- [ ] **Step 2: Wire Logs into App.svelte**

- [ ] **Step 3: Build and verify**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(web): add Logs page with conversation browser, system log, and metrics"
```

---

## Task 9: Remote Page

**Files:**
- Create: `web/src/pages/Remote.svelte`
- Modify: `web/src/App.svelte` (add import + route)

**Reference:** Current remote in `scripts/web/static/remote.js` (356 lines). Features: direct AI query input, response display with audio playback.

- [ ] **Step 1: Write Remote.svelte**

Create `web/src/pages/Remote.svelte`. Key features:

- Text input for direct AI query
- Submit button
- Response display (text + audio player if WAV returned)
- Loading state with bender-cigar.png
- Error state with bender-looking-down.png

- [ ] **Step 2: Wire Remote into App.svelte**

- [ ] **Step 3: Build and verify**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(web): add Remote page with AI query interface"
```

---

## Task 10: Mobile Responsive Pass

**Files:**
- Modify: `web/src/App.svelte`
- Modify: `web/src/lib/components/Sidebar.svelte`

- [ ] **Step 1: Add mobile bottom tab bar**

On screens <768px, the sidebar collapses to a bottom tab bar with icons only. Quick controls move to a pull-up drawer or are accessible from a menu icon.

Update `Sidebar.svelte` with a responsive layout:
- Desktop (md+): full sidebar as currently built
- Mobile (<md): fixed bottom bar with 5 icon buttons

Update `App.svelte` layout:
- Desktop: `flex` with sidebar on left
- Mobile: `flex-col` with main content full width, tab bar fixed at bottom

- [ ] **Step 2: Test at various widths**

Check 375px (phone), 768px (tablet), 1024px+ (desktop).

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(web): add mobile responsive layout with bottom tab bar"
```

---

## Task 11: Cutover — Swap FastAPI Static Mount

**Files:**
- Modify: `scripts/web/app.py:787-790`
- Build: `web/dist/`

- [ ] **Step 1: Final build**

```bash
cd c:/ws/benderpi/web && npm run build
```

- [ ] **Step 2: Update FastAPI static mount**

In `scripts/web/app.py`, replace the static mount block (around lines 787-790):

```python
# Before
if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

# After
_DIST_DIR = os.path.join(_BASE_DIR, "web", "dist")
if os.path.isdir(_DIST_DIR):
    app.mount("/", StaticFiles(directory=_DIST_DIR, html=True), name="static")
```

Also remove the now-unused `_STATIC_DIR` and `_ASSETS_DIR` variable definitions (lines 19-20):

```python
# Remove these lines
_STATIC_DIR = os.path.join(_WEB_DIR, "static")
_ASSETS_DIR = os.path.join(_WEB_DIR, "assets")
```

- [ ] **Step 3: Commit cutover + dist**

```bash
cd c:/ws/benderpi
git add web/dist/ scripts/web/app.py
git commit -m "feat(web): cutover to Svelte UI — serve web/dist/ instead of scripts/web/static/"
```

---

## Task 12: Cleanup — Remove Old Vanilla JS

**Files:**
- Remove: `scripts/web/static/app.js`
- Remove: `scripts/web/static/puppet.js`
- Remove: `scripts/web/static/dashboard.js`
- Remove: `scripts/web/static/config.js`
- Remove: `scripts/web/static/logs.js`
- Remove: `scripts/web/static/remote.js`
- Remove: `scripts/web/static/style.css`
- Remove: `scripts/web/static/index.html`
- Remove: `scripts/web/static/favicon.svg`

- [ ] **Step 1: Remove old files**

```bash
cd c:/ws/benderpi
rm scripts/web/static/app.js scripts/web/static/puppet.js scripts/web/static/dashboard.js
rm scripts/web/static/config.js scripts/web/static/logs.js scripts/web/static/remote.js
rm scripts/web/static/style.css scripts/web/static/index.html scripts/web/static/favicon.svg
```

- [ ] **Step 2: Commit cleanup**

```bash
git add -A scripts/web/static/
git commit -m "chore(web): remove old vanilla JS frontend (replaced by Svelte)"
```

---

## Task 13: Update CLAUDE.md and HANDOVER.md

**Files:**
- Modify: `CLAUDE.md` — update Web UI section with new structure and build commands
- Modify: `HANDOVER.md` — note migration complete, add build workflow

- [ ] **Step 1: Update CLAUDE.md Web UI section**

Replace the current Web UI section with updated paths, build commands, and Svelte project structure.

- [ ] **Step 2: Update HANDOVER.md**

Add to Recent Decisions:
- Svelte + Tailwind UI migration complete — vanilla JS replaced
- Build locally, commit `web/dist/` — no Node.js on Pi
- FastAPI serves `web/dist/` instead of `scripts/web/static/`

- [ ] **Step 3: Commit**

```bash
git commit -m "docs: update CLAUDE.md and HANDOVER.md for Svelte UI migration"
```
