# UI Polish & Fixes — Design Spec

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Three desktop UI improvements: volume control reactivity, desktop layout fix, soundboard clip labels.

---

## Problem Statement

Three UX issues on the BenderPi web admin panel:

1. **Volume slider is laggy** — 300ms debounce on the slider makes dragging feel unresponsive, and rapid changes sometimes don't take effect (overlapping `amixer` subprocess calls race)
2. **Desktop layout broken** — Tab bar floats mid-page with a large empty gap above the content. The soundboard section is pushed to the bottom instead of filling the viewport. Appears to be a CSS layout issue specific to larger screens.
3. **Soundboard clips show filenames** — Puppet mode displays `joke_001`, `confirm_001` instead of the actual speech content. Users can't tell what a clip says without playing it.

---

## Constraints

- Vanilla JS/CSS only — no frameworks, no build step
- All changes must work on both desktop and mobile (phone on local network)
- The `amixer` subprocess approach stays (it's the only way to control WM8960 hardware volume)
- Backward compatible — existing favourites and clip paths must continue to work

---

## 1. Volume Slider Reactivity

### Current State

`app.js` lines 410-422:
- `input` event on the range slider triggers `handleVolumeChange()`
- `handleVolumeChange()` debounces at 300ms, then calls `POST /api/config/volume`
- The API endpoint runs `subprocess.run(["amixer", "-c", "2", "sset", "Speaker", f"{level}%"])`
- No request deduplication — if the user drags quickly, multiple overlapping `amixer` calls race

### Design

**Immediate visual feedback:**
- Update the volume label (`sidebar-vol-label`) on every `input` event, with no debounce. The user sees the percentage change instantly.
- The API call remains debounced — only the visual feedback is instant.

**Reduce debounce to 100ms:**
- 300ms → 100ms. Fast enough to feel responsive, slow enough to avoid flooding the Pi with subprocess calls.

**Request deduplication:**
- Track whether an API call is in-flight with a boolean flag (`_volumeInFlight`).
- If a new value arrives while a call is in-flight, store it as `_volumePending`.
- When the in-flight call completes, if there's a pending value, send it immediately.
- This ensures the final slider position always gets applied, even during rapid dragging.

```
User drags:  10 → 30 → 50 → 70 → 90
Debounce fires at 100ms: sends 30
  → in-flight, stores 50, then 70, then 90 as pending
  → 30 completes, immediately sends 90 (latest pending)
  → result: amixer called twice (30, 90) instead of five times
```

### Files Changed

| File | Changes |
|---|---|
| `scripts/web/static/app.js` | Update `handleVolumeChange()`: instant label update, 100ms debounce, request deduplication |

---

## 2. Desktop Layout Fix

### Current State

On desktop screens, the puppet tab (and likely other tabs) shows a large empty gap between the header/tab bar and the actual content. The tab bar appears to float in the middle of the viewport instead of anchoring below the header.

CSS structure:
- `.app-layout` — flex container (sidebar + main)
- `.main` — `flex: 1; padding: 20px; overflow-x: hidden`
- `.tab-bar` — `height: 42px; border-bottom`
- `.tab-panel.active` — `display: block; overflow-x: hidden`

No obvious fixed-height or absolute-positioning culprit in the CSS. The issue is likely one of:
1. The `.main` container or `.tab-panel` has a `min-height` or `height` that creates dead space
2. The puppet sections (speak input, favourites, all clips) have margins that collapse unexpectedly on wide screens
3. The tab bar is inside a flex child that doesn't align to the top

### Design

**Investigation-first approach** — the exact CSS cause needs to be diagnosed in the browser. The fix will be:

- Ensure `.main` and `.tab-panel` use `display: flex; flex-direction: column` so content fills from the top
- Remove any `min-height` or `height` on containers that would create dead space
- Ensure the tab bar is anchored directly below the header with no gap
- Test on desktop (>1024px) and tablet (768px-1024px) breakpoints
- If the issue is the puppet speak section having a large invisible area, make it compact

### Files Changed

| File | Changes |
|---|---|
| `scripts/web/static/style.css` | Fix desktop layout — ensure content fills from top, no dead space |
| `scripts/web/static/app.js` | Possibly adjust tab panel activation if JS-driven layout is involved |

---

## 3. Soundboard Clip Labels

### Current State

`/api/puppet/clips` returns clips with `name` derived from filenames:
- WAV files: `os.path.splitext(fname)[0]` → `"joke_001"`
- Personal sub-keys: sub_key name → `"age"`, `"where_live"`
- No `label` or `text` field exists in `index.json`

`prebuild_responses.py` has the original text for every TTS-generated clip:
- `PERSONAL_RESPONSES`: dict of sub_key → text
- `JOKE_RESPONSES`: list of joke texts
- `HA_CONFIRM_RESPONSES`: list of confirmation texts
- `PROMOTED_RESPONSES`: list of (slug, pattern, text) tuples
- `THINKING_SOUNDS`, `TIMER_ALERT_RESPONSES`: lists of texts

### Design

**Step 1: Add `labels` to `index.json` during prebuild**

`prebuild_responses.py` already writes `index.json`. Extend it to include the source text as a label for each TTS-generated clip:

```json
{
  "joke": [
    {"file": "speech/responses/joke/joke_001.wav", "label": "Bite my shiny metal ass!"},
    {"file": "speech/responses/joke/joke_002.wav", "label": "I'm 40% jokes!"}
  ],
  "personal": {
    "job": {"file": "speech/responses/personal/job.wav", "label": "I'm a bending unit..."},
    "age": {"file": "speech/responses/personal/age.wav", "label": "I was built in 2996..."}
  },
  "promoted": [
    {"pattern": "...", "file": "speech/responses/promoted/name.wav", "label": "The name's Bender..."}
  ]
}
```

**Schema change:** Entries that were bare strings (`"speech/wav/file.wav"`) become objects (`{"file": "...", "label": "..."}`). Entries that were already objects (promoted) gain a `label` field.

**Original WAV clips** (in `speech/wav/`) don't have source text. For these:
- Add a `clip_labels.json` file in `speech/` that maps filenames to manual descriptions
- `prebuild_responses.py` reads this file and merges labels into `index.json`
- Clips without a label entry fall back to the filename (current behaviour)

**Step 2: Update `_get_clips()` in `app.py`**

Read the `label` field from index entries and include it in the API response:

```python
{
    "path": "speech/responses/joke/joke_001.wav",
    "name": "joke_001",
    "label": "Bite my shiny metal ass!",
    "category": "joke",
    "favourite": false
}
```

For clips without a label, `label` defaults to `name`.

**Step 3: Update `puppet.js` to display labels**

- Use `clip.label` as the button text instead of `clip.name`
- Truncate to ~40 characters with `...` for long labels
- Show full label on hover via `title` attribute (native tooltip)
- Favourites section uses the same label display

**Step 4: Update handler classes to handle new index format**

The new `index.json` format changes bare strings to objects. The handler classes (`RealClipHandler`, `PreGenHandler`, etc.) that read `index.json` must be updated to handle both formats:
- If entry is a string → treat as file path (backward compatible)
- If entry is an object → read `entry["file"]` for the path

### Files Changed

| File | Changes |
|---|---|
| `scripts/prebuild_responses.py` | Write `label` field for all TTS clips; read `clip_labels.json` for manual labels |
| `speech/clip_labels.json` | New file — manual labels for original WAV clips (can start empty, populated over time) |
| `scripts/web/app.py` | `_get_clips()` reads and returns `label` field |
| `scripts/web/static/puppet.js` | Display `label` instead of `name`, with truncation + tooltip |
| `scripts/handlers/clip_handler.py` | Handle both string and object entries in index.json |
| `scripts/handlers/pregen_handler.py` | Handle both string and object entries in index.json |
| `scripts/handlers/promoted_handler.py` | Handle object entries with `label` field (already objects, just ignore `label`) |
| `handler_base.py` | `load_clips_from_index()` handles both string and object entries |

---

## Testing Strategy

- Volume: manual test — drag slider rapidly on desktop, verify label updates instantly and final volume is correct
- Layout: visual test on desktop Chrome/Firefox at 1920px and 1280px widths
- Labels: unit tests for `_get_clips()` with new index format; visual test of puppet soundboard
- Handler backward compat: existing handler tests updated to cover both string and object index entries
