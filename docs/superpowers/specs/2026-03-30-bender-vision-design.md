# Bender Vision Feature — Design Spec

**Date:** 2026-03-30
**Status:** Approved
**Author:** Claude Code (brainstorming session)

---

## Overview

Add camera-based scene awareness to BenderPi. Bender uses the Raspberry Pi AI Camera (IMX500) and Hailo-10H accelerator to detect and describe people in the room — entirely on-device, no cloud vision. He can weave this into conversation, respond to direct questions about what he sees, and optionally make unprompted comments on a toggleable timer.

Long-term: known face recognition per person (Approach C — continuous sensor). This spec covers Approach A (analyse once per session start); the architecture explicitly accommodates C expansion.

---

## Architecture

```
IMX500 camera (on-chip face detection)
     │ picamera2 frame + bounding box metadata
     ▼
vision.py
  ├── capture_frame()              → (frame, bboxes)
  ├── analyse_scene()              → SceneDescription
  │      └── Hailo-10H: age/gender estimation on face crops
  │          (VDevice group_id="SHARED", same as STT/LLM)
  └── VisionWatcher                → background passive mode thread
          ├── checks cfg.vision_passive_enabled
          ├── checks cfg.vision_passive_expires_at
          └── guards against firing during active session

SceneDescription
  ├── faces: list[FaceInfo]
  ├── captured_at: datetime
  └── raw_detections: dict         ← reserved for future Approach C tracking

wake_converse.py  (run_session)
  └── after ai.clear_history()
      ├── vision.analyse_scene()   ← runs in thread during greeting clip
      └── ai.inject_scene_context(scene.to_context_string())
          ai_local.inject_scene_context(...)

handlers/vision_handler.py
  └── intent=VISION → analyse_scene() → LLM commentary → TTS + log

web/app.py
  └── 4 new endpoints (passive toggle, on-demand analyse)
```

---

## Data Structures

```python
@dataclass
class FaceInfo:
    age_estimate: int       # e.g. 35
    gender: str             # "male" / "female"
    confidence: float       # 0.0–1.0
    bbox: tuple             # (x, y, w, h) — stored for future C tracking

@dataclass
class SceneDescription:
    faces: list[FaceInfo]
    captured_at: datetime
    raw_detections: dict    # full Hailo output, reserved for Approach C

    def to_context_string(self) -> str:
        """Returns e.g. 'adult male ~35, child ~8' or 'room appears empty'."""

    def is_empty(self) -> bool:
        """True if no faces detected."""
```

---

## Vision Pipeline

**Hardware split:**
- **IMX500 on-chip:** face detection — outputs bounding boxes via picamera2 post-process metadata alongside the image frame. Leverages the sensor NPU; the right tool for detection. Reserved for continuous operation in future Approach C.
- **Hailo-10H:** age/gender estimation on cropped face regions. Uses `group_id="SHARED"` (consistent with STT and LLM sharing).

**Models required:**
- IMX500: face detection `.rpk` from `imx500-all` (already installed)
- Hailo: `age_gender_estimation` from Hailo model zoo — download via `hailo-download-resources`

> **Pre-implementation check:** Verify `age_gender_estimation` model is available for H10H architecture. Some Hailo zoo models only target H8/H8L. If unavailable, fall back to running face detection entirely on Hailo-10H using an available model.

**Hailo device contention:** Vision analysis runs at session start, before STT begins listening — no contention in practice. Passive mode guards against firing during an active session by checking the session file.

---

## Session Integration

**Injection point:** `run_session()` in `wake_converse.py`, after `ai.clear_history()` and concurrently with greeting clip playback.

```python
# Pseudocode — run_session()
ai.clear_history()
if ai_local:
    ai_local.clear_history()

# Analyse scene concurrently with greeting clip
scene_future = executor.submit(vision.analyse_scene)
_play_greeting(...)
scene = scene_future.result()

ctx = scene.to_context_string()
ai.inject_scene_context(ctx)
if ai_local:
    ai_local.inject_scene_context(ctx)
```

**`inject_scene_context(text: str)`** — new method on both `AIResponder` and `LocalAIResponder`. Stores the string and prepends it as a context note to the first user message of the session. Does not modify `BENDER_SYSTEM_PROMPT`. Cleared automatically by `clear_history()` at session end.

Example injected context: `"[Room: adult male ~35, child ~8]"` prepended to first user turn.

---

## Intent & Handler

**`intent.py`** — new `VISION_PATTERNS` list added to `classify()`, checked before `UNKNOWN` fallback:

```python
VISION_PATTERNS = [
    r"\bwhat (do you see|can you see)\b",
    r"\bwho('?s| is) in the room\b",
    r"\bdescribe (the room|what you see)\b",
    r"\blook around\b",
    r"\bwhat('?s| is) (in front of|around) you\b",
]
```

Returns `("VISION", None)`.

**`handlers/vision_handler.py`** — new handler:
- `intents = ["VISION"]`
- Calls `vision.analyse_scene()`
- Passes `SceneDescription` to LLM (local-first routing) with prompt: describe scene in Bender's voice
- Returns `Response` with TTS wav
- Registered in `Responder.__init__()` alongside existing handlers
- Logged via `session_log.log_turn(..., intent="VISION", method="handler_vision")`

---

## Passive Mode

A daemon `threading.Thread` started in `main()` alongside `briefings.refresh_all`:

```python
threading.Thread(target=_vision_watcher, daemon=True, name="vision-watcher").start()
```

**`_vision_watcher()` loop:**
1. Sleep `cfg.vision_passive_interval_minutes` minutes
2. Check `cfg.vision_passive_enabled` — exit loop if False
3. Check `cfg.vision_passive_expires_at` — if expired, disable and write config, exit
4. Check session file — if session active, skip this cycle
5. `vision.analyse_scene()` — if empty scene, skip
6. Generate Bender commentary via LLM → TTS → play → log as `intent="VISION_PASSIVE"`, `user_text="(passive vision scan)"`

---

## Configuration

New fields added to `Config` class (all overridable via `bender_config.json`):

```python
vision_passive_enabled: bool = False
vision_passive_expires_at: str = ""      # ISO 8601 timestamp, "" = indefinite
vision_passive_interval_minutes: int = 10
```

---

## API Endpoints

All endpoints use `require_pin` auth dependency (consistent with existing pattern).

| Method | Path | Auth | Body / Response |
|--------|------|------|-----------------|
| `POST` | `/api/vision/passive` | pin header | `{"duration_minutes": 30}` or `{"duration_minutes": null}` for indefinite |
| `DELETE` | `/api/vision/passive` | pin header | — |
| `GET` | `/api/vision/passive` | pin header | `{"enabled": bool, "expires_at": str, "minutes_remaining": int\|null}` |
| `POST` | `/api/vision/analyse` | pin header | Returns `{"text": str, "faces": [...]}`, triggers TTS on device |

---

## UI Changes

**`Config.svelte`** — new Vision section:

```
[ Vision ] ──────────────────────────────────────────
  Passive mode   [●──] ON   [15m] [30m] [1h] [3h] [∞]
  Time remaining: 47 minutes
─────────────────────────────────────────────────────
```

- Preset buttons set duration and enable in one action
- Active preset highlighted
- Countdown polls `GET /api/vision/passive` every 60s
- Toggle off calls `DELETE /api/vision/passive`
- Selecting a new preset while active resets timer

**`Puppet.svelte`** — add "Ask Bender" button alongside existing camera stream:
- Calls `POST /api/vision/analyse`
- Displays returned commentary text below the stream
- Bender speaks it on the device simultaneously

---

## Logging

Vision-triggered turns use the existing `session_log.log_turn()` interface:

| Trigger | `user_text` | `intent` | `method` |
|---------|------------|----------|----------|
| Voice command | user's actual words | `VISION` | `handler_vision` |
| UI button | `"(ui: ask bender)"` | `VISION` | `handler_vision` |
| Passive scan | `"(passive vision scan)"` | `VISION_PASSIVE` | `handler_vision` |

All entries written to `logs/YYYY-MM-DD.jsonl` — same file as conversation turns.

---

## Future: Approach C Expansion

The following design decisions explicitly accommodate continuous sensor mode:

1. **`SceneDescription.raw_detections`** — stores full bounding boxes and detection metadata for tracking
2. **`FaceInfo.bbox`** — face position stored per detection; needed for identity tracking across frames
3. **`VisionWatcher`** — designed as a replaceable background concern; Approach C replaces it with a streaming detector publishing to a shared state object, while all downstream consumers (`run_session`, handlers, API) read from `SceneDescription` unchanged
4. **`vision.py` interface** — `analyse_scene()` remains the public API; continuous mode adds `get_current_scene()` reading from a live-updated cache
5. **`hailo-face-recon`** — already installed; used for known-person embeddings in Approach C

Known-person recognition (named faces) is a distinct sub-project: enrol face embeddings per person → match at runtime → pass name into scene context. Scoped separately, builds on top of this spec.
