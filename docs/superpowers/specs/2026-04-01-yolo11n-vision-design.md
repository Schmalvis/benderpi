# YOLO11n Vision Upgrade — Design Spec

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Replace EfficientDet-Lite0 with YOLO11n for multi-class scene detection; expose allowlist via Config UI

---

## Background

BenderPi currently uses EfficientDet-Lite0 on the IMX500 AI camera for person detection only. YOLO11n (mAP 0.374 vs 0.252) detects 80 COCO classes and is available as `imx500_network_yolo11n_pp.rpk` — downloaded to `/usr/share/imx500-models/` as part of this design process.

Tensor format confirmed via live diagnostic:
- `[0]` boxes   `(1, 300, 4)` — pixel coords 0–640
- `[1]` scores  `(1, 300)`    — confidence 0.0–1.0
- `[2]` classes `(1, 300)`    — COCO index 0–79
- `[3]` count   `(1, 1)`      — number of valid detections

Identical structure to EfficientDet-Lite0 (which had 100 slots). Confidence scores top out ~0.22 in typical indoor lighting — threshold lowered to 0.20 (was 0.35).

---

## Goals

1. Swap EfficientDet-Lite0 for YOLO11n in `vision.py`
2. Extend `SceneDescription` to carry all detected object classes, not just people
3. Feed richer scene context into `analyse_scene()` LLM prompt and passive mode
4. Expose confidence threshold and allowlist as editable config in the UI

---

## Data Model

Replace `PersonInfo` with `DetectedObject`:

```python
@dataclass
class DetectedObject:
    label: str        # COCO class name e.g. "person", "bottle", "laptop"
    confidence: float
    bbox: tuple       # pixel coords (x_min, y_min, x_max, y_max)

@dataclass
class SceneDescription:
    objects: list[DetectedObject] = field(default_factory=list)
    captured_at: datetime | None = None

    def persons(self) -> list[DetectedObject]:
        """Convenience: filter to people only (backwards-compatible helper)."""
        return [o for o in self.objects if o.label == "person"]

    def to_context_string(self) -> str:
        """Returns e.g. '[Room: 1 person, 1 laptop, 2 bottles]' or '[Room: empty]'."""
        ...

    def is_empty(self) -> bool:
        return len(self.objects) == 0
```

---

## vision.py Changes

- Model path reads from `cfg.vision_model`, resolved to `/usr/share/imx500-models/imx500_network_{model}_pp.rpk`
- Tensor indices: `_OUT_BOXES=0`, `_OUT_SCORES=1`, `_OUT_CLASSES=2`, `_OUT_COUNT=3`
- Detection loop bounded by `count[0][0]` (up to 300 slots)
- Filter pipeline: confidence >= threshold → label in allowlist (empty allowlist = pass all) → append `DetectedObject`
- COCO class index → name via a module-level lookup dict (all 80 classes)
- Warmup retry unchanged: 25 x 0.3s = 7.5s max

---

## bender_config.json — New Keys

```json
"vision_model": "yolo11n",
"vision_confidence_threshold": 0.20,
"vision_allowlist": [
    "person", "bicycle", "bottle", "wine glass", "cup",
    "chair", "couch", "dining table", "bed", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone",
    "book", "clock", "vase", "potted plant", "teddy bear",
    "backpack", "sports ball", "toothbrush", "dog", "cat"
]
```

`vision_model` resolves the `.rpk` filename. Empty `vision_allowlist` passes all 80 classes.

---

## app.py Changes

`vision_analyse` prompt updated to use richer scene string:

```python
prompt = f"Your camera just scanned the room. {scene.to_context_string()}. React in character."
```

Passive mode watcher requires no change — it already calls `analyse_scene()` -> `to_context_string()`.

---

## Config UI — New Vision Section

New collapsible **Vision** section in `Config.svelte` following existing patterns:

- **Confidence threshold** — number input (0.05–0.50, step 0.05), bound to `$config.vision_confidence_threshold`
- **Allowlist** — toggleable pill UI: all 25 allowlist candidates shown as pills, active = included. Saves as JSON array via existing `PUT /api/config`. No freetext needed — valid universe is finite.

---

## Error Handling

| Condition | Behaviour |
|-----------|-----------|
| Camera acquire fails | Return empty `SceneDescription`, log error |
| Inference warmup timeout | Return empty `SceneDescription`, log info |
| Class index out of range | Skip detection silently |
| Empty allowlist in config | Pass all 80 classes (C-mode) |

---

## Testing

- `tests/test_vision_imx500.py` — update mocks to YOLO11n tensor shape, add allowlist filter tests, add `to_context_string()` multi-class tests
- `tests/test_vision_handler.py` — update `PersonInfo` references to `DetectedObject`

---

## Out of Scope

- Pose estimation / gesture wake
- Age/gender estimation
- Model hot-swap without restart
- YOLOv8n (YOLO11n is strictly better)
