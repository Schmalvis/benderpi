# BenderPi Camera Module 3 + Hailo VLM — Design Spec

**Date:** 2026-04-02
**Status:** Approved
**Author:** Martin Alvis / Claude

---

## Overview

Replace the Raspberry Pi AI Camera (IMX500 + YOLO11n on-sensor inference) with the Camera Module 3 (IMX708) combined with Hailo-10H VLM inference. The IMX500's passive detection capability is redundant because conversation with Bender is always initiated via wake word or UI button. VLM inference provides richer, free-text scene descriptions that feed directly into the LLM as context.

---

## Architecture

Three well-bounded components replace the current `vision.py` monolith:

```
┌─────────────────────────────────────────────────┐
│                  vision.py (public API)          │
│  analyse_scene() → SceneDescription             │
│  acquire_camera() / release_camera()            │
└───────────────┬─────────────────┬───────────────┘
                │                 │
     ┌──────────▼──────┐  ┌───────▼──────────┐
     │  camera.py       │  │  vlm.py           │
     │  Picamera2       │  │  Hailo VLM        │
     │  Camera Mod 3    │  │  inference +      │
     │  frame capture   │  │  timeout guard    │
     └──────────────────┘  └──────────────────┘
```

### `camera.py` (new)
- Owns the Picamera2 singleton for Camera Module 3 (IMX708)
- Thread-safe acquire/release with reference counting
- Returns raw numpy RGB frames on demand
- No knowledge of VLM or inference

### `vlm.py` (new)
- Owns the Hailo VLM lifecycle
- Initialises lazily on first call with `SHARED_VDEVICE_GROUP_ID` (shared device, same group as Whisper STT)
- Preprocesses frames: BGR→RGB, resize to 336×336, uint8
- Calls `generate_all()` with configurable prompt
- Enforces configurable timeout (default 4s) — returns empty string on timeout
- No knowledge of the camera

### `vision.py` (rewrite)
- Thin orchestrator: captures frame via `camera.py`, passes to `vlm.py`, wraps result in `SceneDescription`
- Only public surface for all consumers
- Passive mode removed entirely — no `_vision_watcher`, no passive config

---

## Data Model

```python
@dataclass
class SceneDescription:
    description: str = ""
    captured_at: datetime | None = None

    def is_empty(self) -> bool:
        return not self.description.strip()
```

`DetectedObject`, COCO labels, bbox data, and `to_context_string()` are all removed.

---

## Consumer Changes

### `wake_converse.py`
- `_vision_executor.submit(vision.analyse_scene)` at wake-word — **unchanged**
- Prompt construction changes from `scene.to_context_string()` to `scene.description`
- If `scene.is_empty()`: omit scene context from prompt (same as current empty-room path)

### `vision_handler.py`
- On non-empty scene: passes `scene.description` to LLM as context for in-character response (rather than templating text directly)
- On empty scene: keeps existing in-character quips (no change)

### `web/app.py`
- `_check_camera()`: simplified — acquires/releases Camera Module 3 via `camera.py`, no IMX500 check

---

## Resource Management

**Hailo-10H contention** (VLM + Whisper STT share the device):
- Both use `SHARED_VDEVICE_GROUP_ID` — automatic device queuing, no deadlocks
- VLM runs in a thread with a 4s timeout (`BENDER_VLM_TIMEOUT` env var)
- On timeout: `analyse_scene()` returns empty `SceneDescription` with a warning log
- STT is never blocked waiting on a stale scene capture

**VLM initialisation:**
- Lazy: initialised on first `analyse_scene()` call, kept alive for the process lifetime
- Avoids boot-time cost if vision is never used in a session

---

## Config Additions

```python
# Vision (VLM)
vlm_timeout: float = 4.0          # BENDER_VLM_TIMEOUT
vlm_prompt: str = "Briefly describe what you see in one or two sentences."
```

Config fields removed:
- `vision_model` (was `"yolo11n"`)
- `vision_allowlist`
- `vision_passive_enabled`
- `vision_passive_expires_at`
- `vision_passive_interval_minutes`

`vision_confidence_threshold` — removed (no confidence scores in VLM output).

---

## Implementation Phases

### Phase 1 — Software (IMX500 still installed, bender service expected to fail camera init)

1. Write `camera.py` — Camera Module 3 singleton (Picamera2, no IMX500)
2. Write `vlm.py` — Hailo VLM wrapper with timeout guard
3. Rewrite `vision.py` — orchestrator using `camera.py` + `vlm.py`; delete all IMX500/YOLO/passive code
4. Update `wake_converse.py` — use `scene.description` in LLM prompt
5. Update `vision_handler.py` — route non-empty scenes through LLM
6. Update `web/app.py` — simplify `_check_camera()`
7. Update `config.py` — add VLM config, remove YOLO/passive config
8. Write/update tests

### ⏸ CHECKPOINT — Hardware Swap

> Power down BenderPi. Replace AI Camera (IMX500) with Camera Module 3. Boot up. Confirm swap complete.

### Phase 3 — Validation & Integration

1. Verify Camera Module 3 detected: `libcamera-hello`
2. Test `camera.py` frame capture
3. Test `vlm.py` inference end-to-end (single frame → text)
4. Restart bender service
5. Live wake-word test: confirm scene description flows into Bender's LLM context

---

## Out of Scope

- Passive mode — removed, not replaced
- YOLO / object detection — gone entirely
- IMX500 compatibility shim — none
- Any new UI for vision configuration
