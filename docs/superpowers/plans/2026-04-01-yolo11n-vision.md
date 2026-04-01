# YOLO11n Vision Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace EfficientDet-Lite0 with YOLO11n on the IMX500, extending scene detection from person-only to 80 COCO classes with a configurable allowlist, and exposing threshold/allowlist controls in the Config UI.

**Architecture:** `vision.py` is the single point of change for inference — it owns the model, tensor parsing, COCO label lookup, and `SceneDescription` data model. `config.py` + `bender_config.json` hold the three new tunables. `Config.svelte` gains a Vision section. `app.py` gets a one-line prompt update.

**Tech Stack:** Python 3.13, Picamera2, IMX500 (`imx500_network_yolo11n_pp.rpk` — already downloaded to `/usr/share/imx500-models/`), FastAPI, Svelte, pytest

---

## File Map

| File | Change |
|------|--------|
| `scripts/config.py` | Add `vision_model`, `vision_confidence_threshold`, `vision_allowlist` fields |
| `bender_config.json` | Add default values for the three new fields |
| `scripts/vision.py` | Replace PersonInfo→DetectedObject, add COCO dict, swap model + tensor parser |
| `scripts/web/app.py` | Update `vision_analyse` prompt; include `objects` in API response |
| `tests/test_vision_imx500.py` | Update tensor mocks to YOLO11n shape; add allowlist/multi-class tests |
| `tests/test_vision_handler.py` | Replace `PersonInfo` with `DetectedObject`; update `SceneDescription` constructor calls |
| `web/src/pages/Config.svelte` | Add Vision collapsible section (threshold + allowlist pills) |

---

## Task 1: Add vision config fields

**Files:**
- Modify: `scripts/config.py`
- Modify: `bender_config.json`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_vision_imx500.py` (top of file, before existing tests):

```python
def test_config_vision_defaults():
    """Config exposes vision_model, threshold, and allowlist with sane defaults."""
    import importlib, config as _cfg_mod
    importlib.reload(_cfg_mod)
    from config import Config
    c = Config()
    assert c.vision_model == "yolo11n"
    assert c.vision_confidence_threshold == 0.20
    assert isinstance(c.vision_allowlist, list)
    assert "person" in c.vision_allowlist
    assert "laptop" in c.vision_allowlist
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/pi/bender && source venv/bin/activate
pytest tests/test_vision_imx500.py::test_config_vision_defaults -v
```

Expected: `FAILED — AttributeError: 'Config' object has no attribute 'vision_model'`

- [ ] **Step 3: Add fields to config.py**

In `scripts/config.py`, in the `Config` class body under the `# Vision` section (near line 102), add:

```python
    # Vision — YOLO11n / IMX500
    vision_passive_enabled: bool = False
    vision_passive_expires_at: str = ""
    vision_passive_interval_minutes: int = 10
    vision_model: str = "yolo11n"
    vision_confidence_threshold: float = 0.20
    vision_allowlist: list = None  # set in __init__
```

In `Config.__init__`, after the `if self.ai_routing is None:` block, add:

```python
        if self.vision_allowlist is None:
            self.vision_allowlist = [
                "person", "bicycle", "bottle", "wine glass", "cup",
                "chair", "couch", "dining table", "bed", "tv",
                "laptop", "mouse", "remote", "keyboard", "cell phone",
                "book", "clock", "vase", "potted plant", "teddy bear",
                "backpack", "sports ball", "toothbrush", "dog", "cat",
            ]
```

- [ ] **Step 4: Add defaults to bender_config.json**

In `bender_config.json`, add after the existing vision fields:

```json
  "vision_model": "yolo11n",
  "vision_confidence_threshold": 0.20,
  "vision_allowlist": [
    "person", "bicycle", "bottle", "wine glass", "cup",
    "chair", "couch", "dining table", "bed", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone",
    "book", "clock", "vase", "potted plant", "teddy bear",
    "backpack", "sports ball", "toothbrush", "dog", "cat"
  ],
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_vision_imx500.py::test_config_vision_defaults -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add scripts/config.py bender_config.json
git commit -m "feat: add vision_model, vision_confidence_threshold, vision_allowlist config fields"
```

---

## Task 2: Replace PersonInfo with DetectedObject

**Files:**
- Modify: `scripts/vision.py`
- Modify: `tests/test_vision_imx500.py`

- [ ] **Step 1: Write the failing tests**

Replace the existing `SceneDescription / PersonInfo tests` section in `tests/test_vision_imx500.py` with:

```python
# ---------------------------------------------------------------------------
# DetectedObject / SceneDescription tests (no mocking needed)
# ---------------------------------------------------------------------------

def test_detected_object_fields():
    """DetectedObject holds label, confidence, bbox."""
    from vision import DetectedObject
    obj = DetectedObject(label="person", confidence=0.85, bbox=(10, 20, 100, 200))
    assert obj.label == "person"
    assert obj.confidence == 0.85
    assert obj.bbox == (10, 20, 100, 200)


def test_scene_description_is_empty_when_no_objects():
    from vision import SceneDescription
    assert SceneDescription().is_empty() is True


def test_scene_description_is_not_empty_with_object():
    from vision import SceneDescription, DetectedObject
    scene = SceneDescription(objects=[DetectedObject("person", 0.9, (0, 0, 100, 200))])
    assert scene.is_empty() is False


def test_persons_helper_filters_to_people_only():
    from vision import SceneDescription, DetectedObject
    scene = SceneDescription(objects=[
        DetectedObject("person", 0.9, (0, 0, 100, 200)),
        DetectedObject("laptop", 0.7, (200, 0, 400, 300)),
        DetectedObject("person", 0.6, (400, 0, 640, 400)),
    ])
    persons = scene.persons()
    assert len(persons) == 2
    assert all(p.label == "person" for p in persons)


def test_to_context_string_empty():
    from vision import SceneDescription
    assert SceneDescription().to_context_string() == "[Room: empty]"


def test_to_context_string_single_person():
    from vision import SceneDescription, DetectedObject
    scene = SceneDescription(objects=[DetectedObject("person", 0.9, (0, 0, 100, 200))])
    assert scene.to_context_string() == "[Room: 1 person]"


def test_to_context_string_multi_class():
    from vision import SceneDescription, DetectedObject
    scene = SceneDescription(objects=[
        DetectedObject("person", 0.9, (0, 0, 100, 200)),
        DetectedObject("person", 0.7, (200, 0, 300, 400)),
        DetectedObject("laptop", 0.6, (300, 0, 500, 300)),
        DetectedObject("bottle", 0.5, (400, 0, 450, 200)),
        DetectedObject("bottle", 0.4, (450, 0, 500, 200)),
    ])
    result = scene.to_context_string()
    assert result.startswith("[Room:")
    assert "2 people" in result
    assert "1 laptop" in result
    assert "2 bottles" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_vision_imx500.py -k "detected_object or scene_description or to_context or persons_helper or is_empty or is_not_empty" -v
```

Expected: Multiple `FAILED — ImportError: cannot import name 'DetectedObject'`

- [ ] **Step 3: Update the data model in vision.py**

Replace the `PersonInfo` dataclass and `SceneDescription` class in `scripts/vision.py`:

```python
# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DetectedObject:
    label: str
    confidence: float
    bbox: tuple  # pixel coords (x_min, y_min, x_max, y_max)


@dataclass
class SceneDescription:
    objects: list = field(default_factory=list)
    captured_at: datetime | None = None

    def persons(self) -> list:
        """Return only person detections."""
        return [o for o in self.objects if o.label == "person"]

    def is_empty(self) -> bool:
        return len(self.objects) == 0

    def to_context_string(self) -> str:
        """Returns '[Room: 1 person, 2 bottles, 1 laptop]' or '[Room: empty]'."""
        if not self.objects:
            return "[Room: empty]"
        from collections import Counter
        counts = Counter(o.label for o in self.objects)
        parts = []
        for label, n in sorted(counts.items()):
            if label == "person":
                parts.insert(0, f"{n} {'person' if n == 1 else 'people'}")
            else:
                parts.append(f"{n} {label}")
        return "[Room: " + ", ".join(parts) + "]"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_vision_imx500.py -k "detected_object or scene_description or to_context or persons_helper or is_empty or is_not_empty" -v
```

Expected: All `PASSED`

- [ ] **Step 5: Commit**

```bash
git add scripts/vision.py tests/test_vision_imx500.py
git commit -m "feat: replace PersonInfo with DetectedObject, add multi-class SceneDescription"
```

---

## Task 3: YOLO11n tensor parser

**Files:**
- Modify: `scripts/vision.py`
- Modify: `tests/test_vision_imx500.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_vision_imx500.py`:

```python
# ---------------------------------------------------------------------------
# analyse_scene() with mocked camera — YOLO11n tensors
# ---------------------------------------------------------------------------

import types
import numpy as np

def _make_vision_module():
    """Import vision.py with camera imports mocked out."""
    import importlib, sys
    # Stub heavy deps before import
    for mod in ("picamera2", "picamera2.devices", "picamera2.devices.imx500"):
        if mod not in sys.modules:
            sys.modules[mod] = types.ModuleType(mod)
    pc2 = sys.modules["picamera2"]
    if not hasattr(pc2, "Picamera2"):
        pc2.Picamera2 = lambda *a, **kw: None
    imx_mod = sys.modules["picamera2.devices.imx500"]
    if not hasattr(imx_mod, "IMX500"):
        imx_mod.IMX500 = lambda *a, **kw: None
    import vision
    importlib.reload(vision)
    return vision


def _make_yolo_outputs(boxes, scores, classes, count=None):
    """Build YOLO11n-format get_outputs() return value."""
    n = 300
    b = np.zeros((1, n, 4), dtype=np.float32)
    s = np.zeros((1, n), dtype=np.float32)
    c = np.zeros((1, n), dtype=np.float32)
    valid = len(scores)
    b[0, :valid] = boxes
    s[0, :valid] = scores
    c[0, :valid] = classes
    cnt = np.array([[count if count is not None else valid]], dtype=np.float32)
    return [b, s, c, cnt]


def test_coco_label_lookup():
    """_coco_label returns correct name for known indices."""
    vision = _make_vision_module()
    assert vision._coco_label(0) == "person"
    assert vision._coco_label(39) == "bottle"
    assert vision._coco_label(63) == "laptop"
    assert vision._coco_label(62) == "tv"
    assert vision._coco_label(999) is None  # out of range → None


def test_analyse_scene_filters_by_confidence(monkeypatch):
    """Detections below threshold are excluded."""
    vision = _make_vision_module()
    outputs = _make_yolo_outputs(
        boxes=[[0, 0, 100, 200], [10, 10, 200, 300]],
        scores=[0.25, 0.10],   # first passes 0.20 threshold, second does not
        classes=[0, 0],
    )
    mock_cam = types.SimpleNamespace(
        capture_metadata=lambda: {},
    )
    mock_imx = types.SimpleNamespace(
        get_outputs=lambda meta, add_batch: outputs,
    )
    monkeypatch.setattr(vision, "_cam", mock_cam)
    monkeypatch.setattr(vision, "_imx500", mock_imx)
    monkeypatch.setattr(vision, "_cam_refcount", 1)
    monkeypatch.setattr(vision, "_cam_lock", __import__("threading").Lock())

    scene = vision.analyse_scene()
    assert len(scene.objects) == 1
    assert scene.objects[0].label == "person"
    assert scene.objects[0].confidence == pytest.approx(0.25, abs=0.01)


def test_analyse_scene_filters_by_allowlist(monkeypatch):
    """Objects not in allowlist are excluded."""
    vision = _make_vision_module()
    outputs = _make_yolo_outputs(
        boxes=[[0, 0, 100, 200], [200, 0, 400, 300]],
        scores=[0.8, 0.8],
        classes=[0, 2],   # 0=person (in allowlist), 2=car (not in allowlist)
    )
    mock_cam = types.SimpleNamespace(capture_metadata=lambda: {})
    mock_imx = types.SimpleNamespace(get_outputs=lambda meta, add_batch: outputs)
    monkeypatch.setattr(vision, "_cam", mock_cam)
    monkeypatch.setattr(vision, "_imx500", mock_imx)
    monkeypatch.setattr(vision, "_cam_refcount", 1)
    monkeypatch.setattr(vision, "_cam_lock", __import__("threading").Lock())

    scene = vision.analyse_scene()
    labels = [o.label for o in scene.objects]
    assert "person" in labels
    assert "car" not in labels


def test_analyse_scene_empty_allowlist_passes_all(monkeypatch):
    """Empty allowlist passes all detected classes (C-mode)."""
    vision = _make_vision_module()
    # Temporarily set empty allowlist
    original = vision.cfg.vision_allowlist
    vision.cfg.vision_allowlist = []
    outputs = _make_yolo_outputs(
        boxes=[[0, 0, 100, 200], [200, 0, 400, 300]],
        scores=[0.8, 0.8],
        classes=[0, 2],   # person and car
    )
    mock_cam = types.SimpleNamespace(capture_metadata=lambda: {})
    mock_imx = types.SimpleNamespace(get_outputs=lambda meta, add_batch: outputs)
    monkeypatch.setattr(vision, "_cam", mock_cam)
    monkeypatch.setattr(vision, "_imx500", mock_imx)
    monkeypatch.setattr(vision, "_cam_refcount", 1)
    monkeypatch.setattr(vision, "_cam_lock", __import__("threading").Lock())
    try:
        scene = vision.analyse_scene()
        labels = [o.label for o in scene.objects]
        assert "person" in labels
        assert "car" in labels
    finally:
        vision.cfg.vision_allowlist = original


def test_analyse_scene_returns_empty_on_warmup_timeout(monkeypatch):
    """Returns empty SceneDescription if model not ready after warmup."""
    vision = _make_vision_module()
    mock_cam = types.SimpleNamespace(capture_metadata=lambda: {})
    mock_imx = types.SimpleNamespace(get_outputs=lambda meta, add_batch: None)
    monkeypatch.setattr(vision, "_cam", mock_cam)
    monkeypatch.setattr(vision, "_imx500", mock_imx)
    monkeypatch.setattr(vision, "_cam_refcount", 1)
    monkeypatch.setattr(vision, "_cam_lock", __import__("threading").Lock())
    # Patch sleep to avoid slow test
    monkeypatch.setattr("time.sleep", lambda _: None)
    scene = vision.analyse_scene()
    assert scene.is_empty()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_vision_imx500.py -k "coco_label or analyse_scene" -v
```

Expected: `FAILED — AttributeError: module 'vision' has no attribute '_coco_label'`

- [ ] **Step 3: Rewrite vision.py inference section**

Replace everything from `# IMX500 model configuration` down to the end of `analyse_scene()` in `scripts/vision.py`:

```python
# ---------------------------------------------------------------------------
# IMX500 model configuration
# ---------------------------------------------------------------------------

_OUT_BOXES   = 0
_OUT_SCORES  = 1
_OUT_CLASSES = 2
_OUT_COUNT   = 3

# COCO 80-class index → label
_COCO_CLASSES = {
    0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 4: "airplane",
    5: "bus", 6: "train", 7: "truck", 8: "boat", 9: "traffic light",
    10: "fire hydrant", 11: "stop sign", 12: "parking meter", 13: "bench",
    14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep", 19: "cow",
    20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe", 24: "backpack",
    25: "umbrella", 26: "handbag", 27: "tie", 28: "suitcase", 29: "frisbee",
    30: "skis", 31: "snowboard", 32: "sports ball", 33: "kite",
    34: "baseball bat", 35: "baseball glove", 36: "skateboard",
    37: "surfboard", 38: "tennis racket", 39: "bottle", 40: "wine glass",
    41: "cup", 42: "fork", 43: "knife", 44: "spoon", 45: "bowl",
    46: "banana", 47: "apple", 48: "sandwich", 49: "orange",
    50: "broccoli", 51: "carrot", 52: "hot dog", 53: "pizza",
    54: "donut", 55: "cake", 56: "chair", 57: "couch", 58: "potted plant",
    59: "bed", 60: "dining table", 61: "toilet", 62: "tv", 63: "laptop",
    64: "mouse", 65: "remote", 66: "keyboard", 67: "cell phone",
    68: "microwave", 69: "oven", 70: "toaster", 71: "sink",
    72: "refrigerator", 73: "book", 74: "clock", 75: "vase",
    76: "scissors", 77: "teddy bear", 78: "hair drier", 79: "toothbrush",
}


def _coco_label(idx: int) -> str | None:
    """Return COCO class name for index, or None if out of range."""
    return _COCO_CLASSES.get(int(idx))


def _model_path() -> str:
    return f"/usr/share/imx500-models/imx500_network_{cfg.vision_model}_pp.rpk"


# ---------------------------------------------------------------------------
# Shared camera singleton
# ---------------------------------------------------------------------------

_imx500 = None
_cam = None
_cam_lock = threading.Lock()
_cam_refcount = 0


def _init_camera():
    """Initialise IMX500 + Picamera2. Caller must hold _cam_lock."""
    global _imx500, _cam
    from picamera2 import Picamera2
    from picamera2.devices.imx500 import IMX500

    model = _model_path()
    log.info("Initialising IMX500 with model: %s", model)
    imx500 = IMX500(model)
    cam = Picamera2(imx500.camera_num)
    config = cam.create_video_configuration(
        main={"size": (640, 480), "format": "RGB888"},
        buffer_count=12,
    )
    cam.configure(config)
    try:
        cam.start()
    except Exception:
        cam.close()
        raise
    _imx500 = imx500
    _cam = cam
    log.info("IMX500 camera started")


def acquire_camera():
    """Return the shared Picamera2 instance, starting it if needed."""
    global _cam_refcount
    with _cam_lock:
        if _cam is None:
            _init_camera()
        _cam_refcount += 1
        return _cam


def release_camera():
    """Decrement refcount; stop camera when last consumer disconnects."""
    global _cam, _imx500, _cam_refcount
    with _cam_lock:
        _cam_refcount = max(0, _cam_refcount - 1)
        if _cam_refcount == 0 and _cam is not None:
            try:
                _cam.stop()
                _cam.close()
            except Exception as exc:
                log.warning("Error closing camera: %s", exc)
            _cam = None
            _imx500 = None
            log.info("IMX500 camera stopped")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_scene() -> SceneDescription:
    """Return detected objects using YOLO11n on-device inference."""
    import time as _time
    try:
        cam = acquire_camera()
    except Exception as exc:
        log.error("Failed to acquire camera for analysis: %s", exc)
        return SceneDescription()

    with _cam_lock:
        imx500 = _imx500

    if imx500 is None:
        release_camera()
        return SceneDescription()

    try:
        np_outputs = None
        for _ in range(25):
            metadata = cam.capture_metadata()
            np_outputs = imx500.get_outputs(metadata, add_batch=True)
            if np_outputs is not None:
                break
            _time.sleep(0.3)

        if np_outputs is None:
            log.info("IMX500 inference not ready after warmup — returning empty scene")
            return SceneDescription()

        count = int(np_outputs[_OUT_COUNT][0][0])
        boxes   = np_outputs[_OUT_BOXES][0][:count]
        scores  = np_outputs[_OUT_SCORES][0][:count]
        classes = np_outputs[_OUT_CLASSES][0][:count]

        threshold = cfg.vision_confidence_threshold
        allowlist = cfg.vision_allowlist  # empty list = pass all

        objects = []
        for box, score, cls_idx in zip(boxes, scores, classes):
            score_f = float(score)
            if score_f < threshold:
                continue
            label = _coco_label(cls_idx)
            if label is None:
                continue
            if allowlist and label not in allowlist:
                continue
            objects.append(DetectedObject(
                label=label,
                confidence=score_f,
                bbox=tuple(float(v) for v in box),
            ))

        log.info("Vision: %d object(s) detected: %s",
                 len(objects),
                 ", ".join(o.label for o in objects) if objects else "none")
        return SceneDescription(objects=objects, captured_at=datetime.now())
    finally:
        release_camera()
```

- [ ] **Step 4: Run all vision tests**

```bash
pytest tests/test_vision_imx500.py -v
```

Expected: All `PASSED`

- [ ] **Step 5: Commit**

```bash
git add scripts/vision.py tests/test_vision_imx500.py
git commit -m "feat: YOLO11n tensor parser with COCO lookup and allowlist filtering"
```

---

## Task 4: Update test_vision_handler.py

**Files:**
- Modify: `tests/test_vision_handler.py`

- [ ] **Step 1: Replace PersonInfo with DetectedObject throughout**

Replace the full contents of `tests/test_vision_handler.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch
import pytest


def test_vision_handler_empty_room():
    """Returns a Response when no objects detected."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription
    from datetime import datetime

    empty_scene = SceneDescription(objects=[], captured_at=datetime.now())

    with patch("vision.analyse_scene", return_value=empty_scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("what do you see", "VISION")

    assert resp is not None
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"


def test_vision_handler_with_person():
    """Returns a Response describing a detected person."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription, DetectedObject
    from datetime import datetime

    scene = SceneDescription(
        objects=[DetectedObject(label="person", confidence=0.45, bbox=(10, 20, 200, 400))],
        captured_at=datetime.now(),
    )

    with patch("vision.analyse_scene", return_value=scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("who's in the room", "VISION")

    assert resp is not None
    assert resp.wav_path == "/tmp/test.wav"
    assert "person" in resp.text.lower() or "room" in resp.text.lower()


def test_vision_handler_multiple_persons():
    """Response correctly describes multiple persons."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription, DetectedObject
    from datetime import datetime

    scene = SceneDescription(
        objects=[
            DetectedObject(label="person", confidence=0.45, bbox=(0, 0, 100, 200)),
            DetectedObject(label="person", confidence=0.40, bbox=(150, 0, 300, 200)),
        ],
        captured_at=datetime.now(),
    )

    with patch("vision.analyse_scene", return_value=scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("describe the room", "VISION")

    assert resp is not None
    assert resp.is_temp is True


def test_vision_handler_camera_error():
    """Gracefully handles analyse_scene exceptions."""
    from handlers.vision_handler import VisionHandler

    with patch("vision.analyse_scene", side_effect=RuntimeError("camera offline")), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("look around", "VISION")

    assert resp is not None
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"
```

- [ ] **Step 2: Run test to verify it passes**

```bash
pytest tests/test_vision_handler.py -v
```

Expected: All `PASSED`

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: All tests pass (no regressions).

- [ ] **Step 4: Commit**

```bash
git add tests/test_vision_handler.py
git commit -m "fix: update test_vision_handler to use DetectedObject instead of PersonInfo"
```

---

## Task 5: Update app.py

**Files:**
- Modify: `scripts/web/app.py`

- [ ] **Step 1: Update vision_analyse endpoint**

In `scripts/web/app.py`, find the `vision_analyse` function (around line 934). Make two changes:

**Change 1** — update the prompt lines:

```python
    scene = await asyncio.to_thread(_vision.analyse_scene)
    if scene.is_empty():
        prompt = "Your camera just scanned the room and detected nobody. React in character."
    else:
        prompt = f"Your camera just scanned the room. {scene.to_context_string()}. React in character."
```

**Change 2** — update the return value to expose all objects (not just persons):

```python
    objects = [{"label": o.label, "confidence": round(o.confidence, 3), "bbox": list(o.bbox)}
               for o in scene.objects]
    return {"text": text, "objects": objects}
```

- [ ] **Step 2: Restart service and verify**

```bash
sudo systemctl restart bender-web.service
sleep 3
systemctl is-active bender-web.service
```

Expected: `active`

- [ ] **Step 3: Commit**

```bash
git add scripts/web/app.py
git commit -m "feat: update vision_analyse to use multi-class scene context and objects response"
```

---

## Task 6: Config UI — Vision section

**Files:**
- Modify: `web/src/pages/Config.svelte`

- [ ] **Step 1: Add COCO candidate list constant**

In `Config.svelte`, in the `<script>` block, add after the existing constants:

```javascript
  const VISION_COCO_CANDIDATES = [
    "person", "bicycle", "bottle", "wine glass", "cup",
    "chair", "couch", "dining table", "bed", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone",
    "book", "clock", "vase", "potted plant", "teddy bear",
    "backpack", "sports ball", "toothbrush", "dog", "cat",
  ];
```

- [ ] **Step 2: Add Vision section to the template**

In `Config.svelte`, add a new `<details>` section following the existing pattern. Find the last `</details>` closing tag in the config form and insert after it:

```svelte
<details>
  <summary class={summaryClass}>Vision</summary>
  <div class="space-y-4 pt-2">

    <div>
      <label class={labelClass} for="vision_confidence_threshold">
        Confidence Threshold
      </label>
      <input
        id="vision_confidence_threshold"
        type="number"
        min="0.05" max="0.50" step="0.05"
        class={inputClass}
        bind:value={$config.vision_confidence_threshold}
      />
      <p class="text-[11px] text-text-muted mt-1">
        Minimum detection confidence (0.05–0.50). Lower = more detections, more noise.
      </p>
    </div>

    <div>
      <p class={labelClass}>Detection Allowlist</p>
      <p class="text-[11px] text-text-muted mb-2">
        Active classes are highlighted. Empty = detect all 80 COCO classes.
      </p>
      <div class="flex flex-wrap gap-2">
        {#each VISION_COCO_CANDIDATES as label}
          {@const active = ($config.vision_allowlist ?? []).includes(label)}
          <button
            type="button"
            class="px-2 py-1 rounded text-xs border transition-colors {active
              ? 'bg-accent border-accent text-bg-base'
              : 'bg-bg-input border-border text-text-muted'}"
            on:click={() => {
              const list = [...($config.vision_allowlist ?? [])];
              const idx = list.indexOf(label);
              if (idx >= 0) list.splice(idx, 1);
              else list.push(label);
              $config.vision_allowlist = list;
            }}
          >
            {label}
          </button>
        {/each}
      </div>
    </div>

  </div>
</details>
```

- [ ] **Step 3: Build the frontend**

```bash
cd /home/pi/bender/web && npm run build
```

Expected: Build completes with no errors.

- [ ] **Step 4: Restart service and verify in browser**

```bash
sudo systemctl restart bender-web.service
```

Open the Config page in the browser. Verify:
- Vision section appears and is collapsible
- Confidence threshold input works
- Allowlist pills toggle on/off
- Saving config persists changes (check `bender_config.json` after save)

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Config.svelte web/dist/
git commit -m "feat: add Vision config section with threshold and allowlist pill editor"
```

---

## Task 7: End-to-end smoke test

- [ ] **Step 1: Run full test suite one final time**

```bash
cd /home/pi/bender && source venv/bin/activate
pytest tests/ -v --tb=short
```

Expected: All tests pass.

- [ ] **Step 2: Live camera test**

With `bender-web` running, trigger "Ask Bender" from the UI. Check logs:

```bash
journalctl -u bender-web.service -f | grep -i "Vision\|object\|detected"
```

Expected log line: `Vision: N object(s) detected: person, laptop, bottle` (or similar)

- [ ] **Step 3: Verify Bender responds in character**

Bender should audibly respond with a comment referencing the detected objects — not just a person count.

