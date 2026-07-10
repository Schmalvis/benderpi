# Plan: Migrate Wake Word Detection to openWakeWord

> **SUPERSEDED** — this was the initial high-level scoping pass. The concrete,
> task-by-task implementation plan that was actually executed is
> `docs/superpowers/plans/2026-06-12-openwakeword-migration.md`. Kept here for
> historical context only; do not follow this doc for current setup steps.

**Date:** 2026-05-22  
**Deadline:** 2026-06-30 (Picovoice Free Tier disabled)  
**Context:** docs/checkpoints/2026-05-22-picovoice-sunset.md

---

## Overview

Replace `pvporcupine` with [openWakeWord](https://github.com/dscripka/openWakeWord) — a free, offline, ONNX-based wake word library. No API key, no cloud validation, no vendor lock-in.

The core detection loop in `wait_for_wakeword()` (`wake_converse.py:144–206`) needs a drop-in replacement. Everything outside that function is unaffected.

---

## Key Differences: Porcupine → openWakeWord

| Aspect | Porcupine | openWakeWord |
|---|---|---|
| Auth | API key required | None |
| Model format | `.ppn` (proprietary) | ONNX / tflite |
| Sample rate | 16000 Hz (same) | 16000 Hz |
| Frame size | `porcupine.frame_length` (~512 samples) | 1280 samples (80ms) |
| Detection | Returns index ≥ 0 | Returns score dict (0–1), threshold ~0.5 |
| Custom models | Paid / free tier (sunset) | Open training pipeline (synthetic TTS data) |

---

## The Custom Wake Word Problem

"Hey Bender" has no pre-trained openWakeWord model. Two options:

### Option A — Train a custom model (recommended)
openWakeWord supports training with **synthetic audio data** — no real recordings needed. The training pipeline generates samples via TTS (any voice), so we can train a "hey bender" model without recording sessions.

- Requires: Python training script, ~1–2 hours compute on a desktop/laptop
- Output: an ONNX model file, committed to the repo (gitignored or tracked)
- False positive rate is tunable via threshold

### Option B — Temporary generic wake word
Use a pre-trained model (`hey_jarvis`, `alexa`, `hey_mycroft`) while training runs. Functional immediately, not Bender-branded.

**Recommended path:** Option B to unblock before the deadline, Option A to restore "Hey Bender" afterwards.

---

## Files to Change

### `requirements.txt`
- Remove: `pvporcupine==4.0.2`, `pvrecorder==1.2.7`
- Add: `openwakeword`

### `scripts/wake_converse.py`
Replace the Porcupine block in `wait_for_wakeword()`:

**Remove:**
```python
import pvporcupine
porcupine = pvporcupine.create(
    access_key=os.environ["PORCUPINE_ACCESS_KEY"],
    keyword_paths=[KEYWORD_PATH],
)
# ... porcupine.sample_rate, porcupine.frame_length, porcupine.process(), porcupine.delete()
```

**Replace with:**
```python
from openwakeword.model import Model
oww_model = Model(wakeword_models=[OWW_MODEL_PATH], inference_framework="onnx")
# Sample rate stays 16000 Hz
# Frame size: 1280 samples per chunk instead of porcupine.frame_length
# Detection: oww_model.predict(pcm_np) returns {model_name: score}; trigger on score > threshold
```

The stereo downmix logic (reSpeaker 4-mic) is unchanged — just adjust frame size.

### `scripts/config.py`
- Remove: `porcupine_access_key: str`
- Add: `oww_model_path: str` (path to ONNX model), `oww_threshold: float = 0.5`

### `.env.example`
- Remove: `PORCUPINE_ACCESS_KEY`

### `CLAUDE.md`
- Update environment variables table
- Update wake word detection description

---

## Suggested Approach

1. **Install openWakeWord** on BenderPi, verify it imports cleanly
2. **Validate with a pre-trained model** (hey_jarvis) — confirm detection loop works end-to-end
3. **Train custom "hey bender" model** (can be done on a faster machine, model committed to repo)
4. **Swap to custom model**, tune threshold
5. **Remove Porcupine** from requirements and clean up config/env

---

## Notes

- openWakeWord ONNX inference should run fine on Pi 5 CPU; no Hailo acceleration needed for wake word (low compute)
- The existing PyAudio stream setup and stereo downmix code is reusable unchanged
- Custom model training docs: https://github.com/dscripka/openWakeWord#training-new-models
- Threshold tuning: start at 0.5, raise if false positives, lower if misses
