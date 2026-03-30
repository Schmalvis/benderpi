# Bender Vision Feature — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add on-device camera scene awareness so Bender can describe who's in the room, weave that into conversation, respond to "what do you see?" voice commands, and optionally make unprompted comments on a toggleable timer.

**Architecture:** Picamera2 captures a frame at conversation session start; OpenCV DNN runs face detection and age/gender estimation on CPU (~200-300ms, acceptable for once-per-session). Results are injected as context into the LLM system prompt for the session. A passive mode daemon thread fires independently on a timer. Hailo-10H is not used for vision in Approach A — it stays free for STT and LLM. IMX500 on-chip inference is reserved for future Approach C (continuous sensor).

**Tech Stack:** Python 3.13, OpenCV DNN (Caffe ResNet-10 face detector + AgeNet + GenderNet), picamera2, FastAPI, Svelte, pytest

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `scripts/vision.py` | FaceInfo, SceneDescription, capture_frame, analyse_scene, VisionWatcher |
| Create | `scripts/handlers/vision_handler.py` | VISION intent → analyse → LLM commentary → TTS |
| Create | `tests/test_vision.py` | Unit tests for vision data structures and logic |
| Create | `tests/test_vision_handler.py` | Unit tests for VisionHandler |
| Create | `tests/test_web_vision.py` | API endpoint tests for vision routes |
| Modify | `scripts/config.py` | Add 3 vision_passive_* config fields |
| Modify | `scripts/ai_response.py` | Add inject_scene_context() to AIResponder |
| Modify | `scripts/ai_local.py` | Add inject_scene_context() to LocalAIResponder |
| Modify | `scripts/intent.py` | Add VISION_PATTERNS + classify() branch |
| Modify | `scripts/responder.py` | Import and register VisionHandler |
| Modify | `scripts/wake_converse.py` | Session start vision analysis + VisionWatcher daemon thread |
| Modify | `scripts/web/app.py` | 4 new /api/vision/* endpoints |
| Modify | `web/src/pages/Config.svelte` | Passive mode toggle + preset UI |
| Modify | `web/src/pages/Puppet.svelte` | "Ask Bender" button + commentary display |
| Modify | `web/src/lib/api.js` | visionAnalyse(), visionPassiveGet/Set/Clear() |

---

## Task 1: Download Vision Models

**Files:**
- Create: `models/vision/` directory with 6 model files

All commands run on BenderPi (192.168.68.132) as user `pi`.

- [ ] **Step 1: Verify OpenCV is installed**

```bash
cd /home/pi/bender
source venv/bin/activate
python -c "import cv2; print(cv2.__version__)"
```

Expected: version string like `4.x.x`. If `ModuleNotFoundError`, run:
```bash
pip install opencv-contrib-python-headless
```

- [ ] **Step 2: Create models directory**

```bash
mkdir -p /home/pi/bender/models/vision
cd /home/pi/bender/models/vision
```

- [ ] **Step 3: Download face detection model**

```bash
cd /home/pi/bender/models/vision
wget -q "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt" -O face_deploy.prototxt
wget -q "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20180205_fp16/res10_300x300_ssd_iter_140000_fp16.caffemodel" -O face_net.caffemodel
ls -lh face_deploy.prototxt face_net.caffemodel
```

Expected: `face_deploy.prototxt` (~3KB), `face_net.caffemodel` (~2.7MB)

- [ ] **Step 4: Download age estimation model**

```bash
cd /home/pi/bender/models/vision
wget -q "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/age_deploy.prototxt" -O age_deploy.prototxt
wget -q "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/age_net.caffemodel" -O age_net.caffemodel
ls -lh age_deploy.prototxt age_net.caffemodel
```

Expected: `age_deploy.prototxt` (~1KB), `age_net.caffemodel` (~44MB)

- [ ] **Step 5: Download gender estimation model**

```bash
cd /home/pi/bender/models/vision
wget -q "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/gender_deploy.prototxt" -O gender_deploy.prototxt
wget -q "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/gender_net.caffemodel" -O gender_net.caffemodel
ls -lh gender_deploy.prototxt gender_net.caffemodel
```

Expected: `gender_deploy.prototxt` (~1KB), `gender_net.caffemodel` (~44MB)

- [ ] **Step 6: Smoke test models load correctly**

```bash
cd /home/pi/bender
source venv/bin/activate
python - <<'EOF'
import cv2
import os

base = "models/vision"
face_net = cv2.dnn.readNet(f"{base}/face_net.caffemodel", f"{base}/face_deploy.prototxt")
age_net = cv2.dnn.readNet(f"{base}/age_net.caffemodel", f"{base}/age_deploy.prototxt")
gender_net = cv2.dnn.readNet(f"{base}/gender_net.caffemodel", f"{base}/gender_deploy.prototxt")
print("All models loaded OK")
EOF
```

Expected: `All models loaded OK`

- [ ] **Step 7: Commit models directory marker**

```bash
cd /home/pi/bender
echo "*.caffemodel" >> .gitignore
git add models/vision/face_deploy.prototxt models/vision/age_deploy.prototxt models/vision/gender_deploy.prototxt .gitignore
git commit -m "chore: add vision model protobufs and gitignore caffemodels"
```

---

## Task 2: Vision Data Structures

**Files:**
- Create: `scripts/vision.py`
- Create: `tests/test_vision.py`

- [ ] **Step 1: Write failing tests for SceneDescription**

Create `tests/test_vision.py`:

```python
"""Tests for vision data structures."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from datetime import datetime
from unittest.mock import patch
import pytest


def make_scene(faces=None):
    """Import SceneDescription after patching heavy deps."""
    with patch.dict('sys.modules', {
        'cv2': __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(),
        'picamera2': __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(),
    }):
        from vision import FaceInfo, SceneDescription
        return FaceInfo, SceneDescription, faces or []


def test_scene_empty_string():
    FaceInfo, SceneDescription, _ = make_scene()
    scene = SceneDescription(faces=[], captured_at=datetime.now(), raw_detections={})
    assert scene.to_context_string() == "room appears empty"


def test_scene_is_empty_true():
    FaceInfo, SceneDescription, _ = make_scene()
    scene = SceneDescription(faces=[], captured_at=datetime.now(), raw_detections={})
    assert scene.is_empty() is True


def test_scene_single_adult_male():
    FaceInfo, SceneDescription, _ = make_scene()
    face = FaceInfo(age_estimate=35, gender="male", confidence=0.9, bbox=(10, 10, 50, 50))
    scene = SceneDescription(faces=[face], captured_at=datetime.now(), raw_detections={})
    assert scene.to_context_string() == "adult male ~35"
    assert scene.is_empty() is False


def test_scene_child_detection():
    FaceInfo, SceneDescription, _ = make_scene()
    face = FaceInfo(age_estimate=8, gender="female", confidence=0.85, bbox=(0, 0, 40, 40))
    scene = SceneDescription(faces=[face], captured_at=datetime.now(), raw_detections={})
    assert "child" in scene.to_context_string()
    assert "female" in scene.to_context_string()


def test_scene_multiple_faces():
    FaceInfo, SceneDescription, _ = make_scene()
    faces = [
        FaceInfo(age_estimate=35, gender="male", confidence=0.9, bbox=(0, 0, 50, 50)),
        FaceInfo(age_estimate=8, gender="male", confidence=0.85, bbox=(60, 0, 50, 50)),
    ]
    scene = SceneDescription(faces=faces, captured_at=datetime.now(), raw_detections={})
    ctx = scene.to_context_string()
    assert "adult male ~35" in ctx
    assert "child male ~8" in ctx
    assert ", " in ctx


def test_scene_teen_boundary():
    """Ages 13-17 should be 'teen', not 'child' or 'adult'."""
    FaceInfo, SceneDescription, _ = make_scene()
    face = FaceInfo(age_estimate=15, gender="female", confidence=0.8, bbox=(0, 0, 50, 50))
    scene = SceneDescription(faces=[face], captured_at=datetime.now(), raw_detections={})
    ctx = scene.to_context_string()
    assert "teen" in ctx
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_vision.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'vision'` or similar import error.

- [ ] **Step 3: Create vision.py with data structures**

Create `scripts/vision.py`:

```python
"""Vision module — scene analysis via OpenCV DNN.

Approach A: capture once per session start, run inference on CPU.
Future Approach C: swap VisionWatcher for continuous IMX500 on-chip detector.

Public API (stable across A→C):
  analyse_scene() -> SceneDescription
  capture_frame() -> tuple[np.ndarray, list[tuple]]
  VisionWatcher   — background passive mode thread
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_DIR = os.path.join(_BASE_DIR, "models", "vision")

# Age bucket midpoints for the 7-class AgeNet model
_AGE_BUCKETS = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']
_AGE_MIDPOINTS = [1, 5, 10, 18, 29, 41, 51, 80]
_GENDER_CLASSES = ['male', 'female']
_MODEL_MEAN = (78.4263377603, 87.7689143744, 114.895847746)


@dataclass
class FaceInfo:
    age_estimate: int        # e.g. 35
    gender: str              # "male" / "female"
    confidence: float        # detection confidence 0.0–1.0
    bbox: tuple              # (x, y, w, h) — stored for future Approach C tracking


@dataclass
class SceneDescription:
    faces: list              # list[FaceInfo]
    captured_at: datetime
    raw_detections: dict = field(default_factory=dict)  # reserved for Approach C

    def to_context_string(self) -> str:
        """Human-readable scene summary for LLM context injection."""
        if not self.faces:
            return "room appears empty"
        parts = []
        for f in self.faces:
            if f.age_estimate < 13:
                age_label = "child"
            elif f.age_estimate < 18:
                age_label = "teen"
            else:
                age_label = "adult"
            parts.append(f"{age_label} {f.gender} ~{f.age_estimate}")
        return ", ".join(parts)

    def is_empty(self) -> bool:
        return len(self.faces) == 0


# ── Model loading (lazy, cached) ─────────────────────────────────────────────

_face_net: Optional[cv2.dnn.Net] = None
_age_net: Optional[cv2.dnn.Net] = None
_gender_net: Optional[cv2.dnn.Net] = None
_models_lock = threading.Lock()


def _load_models() -> tuple:
    """Load and cache OpenCV DNN models. Thread-safe."""
    global _face_net, _age_net, _gender_net
    with _models_lock:
        if _face_net is None:
            _face_net = cv2.dnn.readNet(
                os.path.join(_MODEL_DIR, "face_net.caffemodel"),
                os.path.join(_MODEL_DIR, "face_deploy.prototxt"),
            )
            _age_net = cv2.dnn.readNet(
                os.path.join(_MODEL_DIR, "age_net.caffemodel"),
                os.path.join(_MODEL_DIR, "age_deploy.prototxt"),
            )
            _gender_net = cv2.dnn.readNet(
                os.path.join(_MODEL_DIR, "gender_net.caffemodel"),
                os.path.join(_MODEL_DIR, "gender_deploy.prototxt"),
            )
    return _face_net, _age_net, _gender_net


# ── Frame capture ─────────────────────────────────────────────────────────────

def capture_frame() -> tuple:
    """Capture a single RGB frame from the Pi Camera. Returns (frame_bgr, []).

    Returns (None, []) if camera is unavailable.
    The empty list is the bounding-box placeholder — future Approach C will
    return on-chip IMX500 detections here instead.
    """
    try:
        from picamera2 import Picamera2
        import io
        from PIL import Image as PILImage

        cam = Picamera2()
        cfg_cam = cam.create_still_configuration(main={"size": (640, 480), "format": "RGB888"})
        cam.configure(cfg_cam)
        cam.start()
        time.sleep(0.5)  # let sensor settle
        frame_rgb = cam.capture_array()
        cam.stop()
        cam.close()

        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        return frame_bgr, []
    except Exception:
        return None, []


# ── Face detection + age/gender ───────────────────────────────────────────────

def _detect_faces(frame_bgr: np.ndarray, face_net: cv2.dnn.Net) -> list:
    """Run SSD face detector. Returns list of (x, y, w, h) tuples."""
    h, w = frame_bgr.shape[:2]
    blob = cv2.dnn.blobFromImage(frame_bgr, 1.0, (300, 300), _MODEL_MEAN)
    face_net.setInput(blob)
    detections = face_net.forward()
    faces = []
    for i in range(detections.shape[2]):
        confidence = float(detections[0, 0, i, 2])
        if confidence < 0.5:
            continue
        x1 = int(detections[0, 0, i, 3] * w)
        y1 = int(detections[0, 0, i, 4] * h)
        x2 = int(detections[0, 0, i, 5] * w)
        y2 = int(detections[0, 0, i, 6] * h)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            faces.append((x1, y1, x2 - x1, y2 - y1, confidence))
    return faces


def _estimate_age_gender(face_crop: np.ndarray, age_net: cv2.dnn.Net, gender_net: cv2.dnn.Net) -> tuple:
    """Run AgeNet + GenderNet on a face crop. Returns (age_int, gender_str)."""
    blob = cv2.dnn.blobFromImage(face_crop, 1.0, (227, 227), _MODEL_MEAN, swapRB=False)

    gender_net.setInput(blob)
    gender_preds = gender_net.forward()
    gender = _GENDER_CLASSES[gender_preds[0].argmax()]

    age_net.setInput(blob)
    age_preds = age_net.forward()
    age = _AGE_MIDPOINTS[age_preds[0].argmax()]

    return age, gender


def analyse_scene() -> SceneDescription:
    """Capture frame, detect faces, estimate age/gender. Returns SceneDescription.

    Safe to call from a thread. Returns empty SceneDescription on any error.
    """
    try:
        frame_bgr, _ = capture_frame()
        if frame_bgr is None:
            return SceneDescription(faces=[], captured_at=datetime.now(timezone.utc))

        face_net, age_net, gender_net = _load_models()
        raw_faces = _detect_faces(frame_bgr, face_net)

        face_infos = []
        raw_detections = {"faces_raw": []}
        for (x, y, w, h, conf) in raw_faces:
            crop = frame_bgr[y:y + h, x:x + w]
            if crop.size == 0:
                continue
            age, gender = _estimate_age_gender(crop, age_net, gender_net)
            face_infos.append(FaceInfo(
                age_estimate=age,
                gender=gender,
                confidence=conf,
                bbox=(x, y, w, h),
            ))
            raw_detections["faces_raw"].append({"bbox": (x, y, w, h), "conf": conf})

        return SceneDescription(
            faces=face_infos,
            captured_at=datetime.now(timezone.utc),
            raw_detections=raw_detections,
        )
    except Exception:
        return SceneDescription(faces=[], captured_at=datetime.now(timezone.utc))


# ── Passive mode watcher ──────────────────────────────────────────────────────

class VisionWatcher:
    """Background daemon that periodically analyses the scene when passive mode is on.

    Designed as a swappable unit — Approach C replaces the body of _loop()
    with a streaming IMX500 detector while all callers remain unchanged.
    """

    def __init__(self, on_scene, session_active_fn, config):
        """
        on_scene: callable(SceneDescription) — called when a non-empty scene is detected
        session_active_fn: callable() -> bool — returns True if a conversation is active
        config: cfg object with vision_passive_* fields
        """
        self._on_scene = on_scene
        self._session_active = session_active_fn
        self._cfg = config
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="vision-watcher")
        self._thread.start()

    def _loop(self):
        from logger import get_logger
        log = get_logger("vision_watcher")

        while True:
            interval = self._cfg.vision_passive_interval_minutes * 60
            time.sleep(interval)

            if not self._cfg.vision_passive_enabled:
                log.info("Vision passive mode disabled — watcher exiting")
                return

            # Check expiry
            if self._cfg.vision_passive_expires_at:
                from datetime import datetime, timezone
                import json
                try:
                    expires = datetime.fromisoformat(self._cfg.vision_passive_expires_at)
                    if datetime.now(timezone.utc) >= expires:
                        log.info("Vision passive mode expired — disabling")
                        self._cfg.vision_passive_enabled = False
                        self._cfg.vision_passive_expires_at = ""
                        self._cfg.save()
                        return
                except ValueError:
                    pass

            # Don't interrupt active sessions
            if self._session_active():
                log.debug("Session active — skipping passive vision scan")
                continue

            scene = analyse_scene()
            if not scene.is_empty():
                self._on_scene(scene)
```

- [ ] **Step 4: Run tests**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_vision.py -v
```

Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender
git add scripts/vision.py tests/test_vision.py
git commit -m "feat: add vision data structures and OpenCV DNN inference pipeline"
```

---

## Task 3: Config Additions

**Files:**
- Modify: `scripts/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_config.py` (append to existing file):

```python
def test_vision_passive_defaults():
    from config import BenderConfig
    cfg = BenderConfig.__new__(BenderConfig)
    # Check defaults exist
    assert hasattr(cfg, 'vision_passive_enabled')
    assert hasattr(cfg, 'vision_passive_expires_at')
    assert hasattr(cfg, 'vision_passive_interval_minutes')


def test_vision_passive_from_json(tmp_path):
    import json
    from config import BenderConfig
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text(json.dumps({
        "vision_passive_enabled": True,
        "vision_passive_interval_minutes": 5,
    }))
    cfg = BenderConfig(config_path=str(cfg_file))
    assert cfg.vision_passive_enabled is True
    assert cfg.vision_passive_interval_minutes == 5
    assert cfg.vision_passive_expires_at == ""  # default


def test_config_save(tmp_path):
    import json
    from config import BenderConfig
    cfg_file = tmp_path / "bender_config.json"
    cfg_file.write_text("{}")
    cfg = BenderConfig(config_path=str(cfg_file))
    cfg._config_path = str(cfg_file)
    cfg.vision_passive_enabled = True
    cfg.save()
    data = json.loads(cfg_file.read_text())
    assert data["vision_passive_enabled"] is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_config.py::test_vision_passive_defaults tests/test_config.py::test_vision_passive_from_json tests/test_config.py::test_config_save -v
```

Expected: FAIL — attributes don't exist, no `save()` method.

- [ ] **Step 3: Add fields and save() to config.py**

In `scripts/config.py`, find the class body where other config fields are declared (near `dismissal_ends_session`) and add:

```python
    # Vision
    vision_passive_enabled: bool = False
    vision_passive_expires_at: str = ""       # ISO 8601 timestamp, "" = indefinite
    vision_passive_interval_minutes: int = 10
```

Then in `__init__`, after the `ha_room_synonyms` line, add:

```python
        self.vision_passive_enabled: bool = overrides.get("vision_passive_enabled", False)
        self.vision_passive_expires_at: str = overrides.get("vision_passive_expires_at", "")
        self.vision_passive_interval_minutes: int = overrides.get("vision_passive_interval_minutes", 10)
        self._config_path: str = path
```

Add a `save()` method to the `Config` class (after `__init__`):

```python
    def save(self) -> None:
        """Persist runtime-editable fields back to bender_config.json."""
        import json
        _RUNTIME_FIELDS = [
            "vision_passive_enabled",
            "vision_passive_expires_at",
            "vision_passive_interval_minutes",
        ]
        try:
            with open(self._config_path) as f:
                data = json.load(f)
        except Exception:
            data = {}
        for field in _RUNTIME_FIELDS:
            data[field] = getattr(self, field)
        with open(self._config_path, "w") as f:
            json.dump(data, f, indent=2)
```

- [ ] **Step 4: Run tests**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_config.py -v
```

Expected: All tests pass (including pre-existing ones).

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender
git add scripts/config.py tests/test_config.py
git commit -m "feat: add vision passive mode config fields and save()"
```

---

## Task 4: LLM Context Injection

**Files:**
- Modify: `scripts/ai_response.py`
- Modify: `scripts/ai_local.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_vision_context.py`:

```python
"""Tests for scene context injection into LLM responders."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from unittest.mock import patch, MagicMock
import pytest


def test_ai_responder_inject_context():
    with patch('anthropic.Anthropic'):
        from ai_response import AIResponder
        ai = AIResponder()
        ai.inject_scene_context("adult male ~35, child ~8")
        assert ai._scene_context == "adult male ~35, child ~8"


def test_ai_responder_context_prepended_to_first_message():
    with patch('anthropic.Anthropic') as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Beer me!")]
        mock_client.messages.create.return_value = mock_msg

        from ai_response import AIResponder
        ai = AIResponder()
        ai.inject_scene_context("adult male ~35")
        ai.respond("What time is it?")

        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs.get("messages") or call_args.args[2] if call_args.args else call_args.kwargs["messages"]
        first_user = next(m for m in messages if m["role"] == "user")
        assert "[Room: adult male ~35]" in first_user["content"]
        assert "What time is it?" in first_user["content"]


def test_ai_responder_context_cleared_with_history():
    with patch('anthropic.Anthropic'):
        from ai_response import AIResponder
        ai = AIResponder()
        ai.inject_scene_context("adult male ~35")
        ai.clear_history()
        assert ai._scene_context == ""


def test_local_ai_responder_inject_context():
    with patch('hailo_platform.VDevice'), patch('hailo_platform.HailoSchedulingAlgorithm'):
        try:
            from ai_local import LocalAIResponder
            ai = LocalAIResponder.__new__(LocalAIResponder)
            ai._scene_context = ""
            ai.inject_scene_context("child ~8")
            assert ai._scene_context == "child ~8"
        except Exception:
            pytest.skip("Hailo not available in test environment")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_vision_context.py -v 2>&1 | head -20
```

Expected: `AttributeError: 'AIResponder' object has no attribute 'inject_scene_context'`

- [ ] **Step 3: Add inject_scene_context to AIResponder**

In `scripts/ai_response.py`, find `__init__` and add `self._scene_context = ""` after `self.history = []`:

```python
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        self.history = []
        self._scene_context = ""          # ← add this line
```

Add new method after `__init__`:

```python
    def inject_scene_context(self, context: str) -> None:
        """Store scene description; prepended to first user message this session."""
        self._scene_context = context
```

In `respond()`, find where user message is appended to `self.history` (the line `self.history.append({"role": "user", "content": user_text})`), and replace it with:

```python
        if self._scene_context and not self.history:
            # Prepend room context to the very first user message of the session
            content = f"[Room: {self._scene_context}]\n{user_text}"
        else:
            content = user_text
        self.history.append({"role": "user", "content": content})
        self._trim_history()
```

In `clear_history()`, add `self._scene_context = ""`:

```python
    def clear_history(self):
        """Call at end of each conversation session."""
        self.history = []
        self._scene_context = ""          # ← add this line
```

Apply the same pattern in the streaming `respond_streaming()` method — find the equivalent `self.history.append({"role": "user", ...})` line and apply the same context-prepend logic.

- [ ] **Step 4: Add inject_scene_context to LocalAIResponder**

In `scripts/ai_local.py`, in `_HailoLLMResponder.__init__`, add `self._scene_context = ""` alongside the existing `_context_active` flag:

```python
        self._scene_context = ""          # ← add this line
        self._context_active = False
```

Add method to `_HailoLLMResponder`:

```python
    def inject_scene_context(self, context: str) -> None:
        self._scene_context = context
```

In `_HailoLLMResponder.generate()`, find where `messages` is constructed. When `not self._context_active`, modify the user message content to include scene context:

```python
        if not self._context_active:
            user_content = text
            if self._scene_context:
                user_content = f"[Room: {self._scene_context}]\n{text}"
            messages = [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_content},
            ]
        else:
            messages = [{"role": "user", "content": self.history[-1]["content"]}]
```

In `clear_history()` of `_HailoLLMResponder`, add `self._scene_context = ""`.

Add `inject_scene_context` to `LocalAIResponder` (the wrapper class) delegating to the active responder:

```python
    def inject_scene_context(self, context: str) -> None:
        if self._hailo:
            self._hailo.inject_scene_context(context)
        # OllamaResponder doesn't need it — system prompt is sent every call
```

- [ ] **Step 5: Run tests**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_vision_context.py tests/test_ai_local.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
cd /home/pi/bender
git add scripts/ai_response.py scripts/ai_local.py tests/test_vision_context.py
git commit -m "feat: add inject_scene_context to AIResponder and LocalAIResponder"
```

---

## Task 5: Intent Routing

**Files:**
- Modify: `scripts/intent.py`
- Modify: `tests/test_intent.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_intent.py` (append to existing test file):

```python
def test_vision_what_do_you_see():
    from intent import classify
    assert classify("what do you see") == ("VISION", None)


def test_vision_who_is_in_room():
    from intent import classify
    assert classify("who's in the room") == ("VISION", None)


def test_vision_describe_room():
    from intent import classify
    assert classify("describe the room") == ("VISION", None)


def test_vision_look_around():
    from intent import classify
    assert classify("look around") == ("VISION", None)


def test_vision_what_can_you_see():
    from intent import classify
    assert classify("what can you see") == ("VISION", None)


def test_vision_does_not_catch_unrelated():
    from intent import classify
    intent, _ = classify("set a timer for 5 minutes")
    assert intent != "VISION"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_intent.py::test_vision_what_do_you_see tests/test_intent.py::test_vision_who_is_in_room -v
```

Expected: FAIL — intent returns `("UNKNOWN", None)`.

- [ ] **Step 3: Add VISION_PATTERNS to intent.py**

In `scripts/intent.py`, after the existing pattern lists (e.g. after `JOKE_PATTERNS`), add:

```python
VISION_PATTERNS = [
    r"\bwhat (do|can) you see\b",
    r"\bwho'?s? in the room\b",
    r"\bdescribe (the room|what you see)\b",
    r"\blook around\b",
    r"\bwhat'?s? (in front of|around) you\b",
    r"\bwhat do you think of (my|this|the)\b",
    r"\bcan you see (me|us|anyone)\b",
]
```

In `classify()`, add the check before the `UNKNOWN` fallback (after `NEWS_PATTERNS` check and before the `CONTEXTUAL` section):

```python
    if _match_any(t, VISION_PATTERNS):
        return ("VISION", None)
```

Also add `"VISION"` to `_check_all_intents()`:

```python
    if _match_any(t, VISION_PATTERNS):
        matched.append("VISION")
```

- [ ] **Step 4: Run tests**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_intent.py -v
```

Expected: All pass including pre-existing tests.

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender
git add scripts/intent.py tests/test_intent.py
git commit -m "feat: add VISION intent patterns to intent router"
```

---

## Task 6: Vision Handler

**Files:**
- Create: `scripts/handlers/vision_handler.py`
- Create: `tests/test_vision_handler.py`
- Modify: `scripts/responder.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_vision_handler.py`:

```python
"""Tests for VisionHandler."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from unittest.mock import patch, MagicMock
from datetime import datetime
import pytest


def _make_handler():
    with patch.dict('sys.modules', {
        'cv2': MagicMock(),
        'picamera2': MagicMock(),
    }):
        from handlers.vision_handler import VisionHandler
        return VisionHandler()


def test_vision_handler_declares_intent():
    h = _make_handler()
    assert "VISION" in h.intents


def test_vision_handler_returns_none_for_wrong_intent():
    h = _make_handler()
    result = h.handle("what's the weather", "WEATHER")
    assert result is None


def test_vision_handler_calls_analyse_scene():
    with patch.dict('sys.modules', {'cv2': MagicMock(), 'picamera2': MagicMock()}):
        from datetime import datetime, timezone
        mock_scene = MagicMock()
        mock_scene.to_context_string.return_value = "adult male ~35"
        mock_scene.is_empty.return_value = False

        with patch('handlers.vision_handler.analyse_scene', return_value=mock_scene), \
             patch('handlers.vision_handler.tts_generate') as mock_tts:
            mock_tts.speak.return_value = "/tmp/fake.wav"
            from handlers.vision_handler import VisionHandler
            h = VisionHandler()

            # Mock the AI responder
            mock_ai = MagicMock()
            mock_ai.respond.return_value = "/tmp/fake.wav"
            result = h.handle("what do you see", "VISION", ai=mock_ai)

            assert result is not None
            assert mock_scene.to_context_string.called


def test_vision_handler_empty_scene_response():
    with patch.dict('sys.modules', {'cv2': MagicMock(), 'picamera2': MagicMock()}):
        mock_scene = MagicMock()
        mock_scene.is_empty.return_value = True
        mock_scene.to_context_string.return_value = "room appears empty"

        with patch('handlers.vision_handler.analyse_scene', return_value=mock_scene), \
             patch('handlers.vision_handler.tts_generate') as mock_tts:
            mock_tts.speak.return_value = "/tmp/empty.wav"
            from handlers.vision_handler import VisionHandler
            h = VisionHandler()
            mock_ai = MagicMock()
            result = h.handle("what do you see", "VISION", ai=mock_ai)
            assert result is not None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_vision_handler.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'handlers.vision_handler'`

- [ ] **Step 3: Create vision_handler.py**

Create `scripts/handlers/vision_handler.py`:

```python
"""Vision handler — responds to VISION intent and passive scan commentary."""
from __future__ import annotations

import os

import tts_generate
from handler_base import Handler, Response
from logger import get_logger
from vision import analyse_scene, SceneDescription

log = get_logger("vision_handler")

_EMPTY_SCENE_PROMPTS = [
    "Nobody's here. Just me, my thoughts, and my profound disappointment.",
    "The room appears to be empty. Finally, some peace and quiet.",
    "I can't see anyone. Either they left or they're invisible. Both would be fine.",
]

_VISION_PROMPT_TEMPLATE = """You are Bender Bending Rodriguez. You can see the following people in the room: {scene}.
Make ONE short, sarcastic, in-character comment about what you see. Stay in character. Max 2 sentences.
Examples:
- For "adult male ~35": "A middle-aged meatbag. I've seen better. I've also seen worse, but mostly better."
- For "child ~8, adult female ~30": "A small human and a slightly larger human. How delightful. Or whatever."
Keep it Bender. Keep it short."""


class VisionHandler(Handler):
    intents = ["VISION"]

    def handle(self, text: str, intent: str, sub_key: str = None, ai=None, **kwargs) -> Response | None:
        if intent != "VISION":
            return None

        log.info("Vision handler triggered by: %r", text)
        scene = analyse_scene()
        return self._build_response(scene, ai, intent, text)

    def handle_passive(self, scene: SceneDescription, ai=None) -> Response | None:
        """Called by VisionWatcher for passive mode comments."""
        return self._build_response(scene, ai, "VISION_PASSIVE", "(passive vision scan)")

    def _build_response(self, scene: SceneDescription, ai, intent: str, user_text: str) -> Response:
        import random

        if scene.is_empty():
            text = random.choice(_EMPTY_SCENE_PROMPTS)
            wav = tts_generate.speak(text)
            return Response(
                text=text, wav_path=wav, method="handler_vision",
                intent=intent, sub_key=None, is_temp=True,
            )

        prompt = _VISION_PROMPT_TEMPLATE.format(scene=scene.to_context_string())

        if ai is not None:
            try:
                wav = ai.respond(prompt)
                # ai.respond returns wav path for cloud, text for local
                if isinstance(wav, str) and os.path.exists(wav):
                    return Response(
                        text=prompt, wav_path=wav, method="handler_vision",
                        intent=intent, sub_key=None, is_temp=True,
                    )
            except Exception as e:
                log.warning("AI vision response failed: %s", e)

        # Fallback: TTS a canned line
        text = f"I see {scene.to_context_string()}. Very interesting. Not really."
        wav = tts_generate.speak(text)
        return Response(
            text=text, wav_path=wav, method="handler_vision",
            intent=intent, sub_key=None, is_temp=True,
        )
```

- [ ] **Step 4: Register VisionHandler in responder.py**

In `scripts/responder.py`, find the imports block inside `Responder.__init__()` and add:

```python
        from handlers.vision_handler import VisionHandler
```

Add `VisionHandler()` to the `handlers` list (after `TimerHandler()`):

```python
        handlers = [
            RealClipHandler(index_path=idx_path, base_dir=self._base_dir),
            PreGenHandler(index_path=idx_path, base_dir=self._base_dir),
            PromotedHandler(index_path=idx_path, base_dir=self._base_dir),
            ContextualHandler(),
            WeatherHandler(),
            NewsHandler(),
            HAHandler(),
            TimerHandler(),
            VisionHandler(),                # ← add this
        ]
```

- [ ] **Step 5: Run tests**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_vision_handler.py tests/test_responder.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
cd /home/pi/bender
git add scripts/handlers/vision_handler.py scripts/responder.py tests/test_vision_handler.py
git commit -m "feat: add VisionHandler and register with responder"
```

---

## Task 7: Session Start Integration

**Files:**
- Modify: `scripts/wake_converse.py`

- [ ] **Step 1: Add concurrent.futures import**

In `scripts/wake_converse.py`, find the stdlib imports block and add:

```python
import concurrent.futures
```

- [ ] **Step 2: Add vision import**

After the existing script imports (near `from responder import Responder`), add:

```python
try:
    import vision as _vision
    _VISION_AVAILABLE = True
except Exception:
    _VISION_AVAILABLE = False
```

- [ ] **Step 3: Add vision executor**

After the existing module-level constants (near `SILENCE_TIMEOUT`), add:

```python
_vision_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision")
```

- [ ] **Step 4: Inject scene context in run_session()**

In `run_session()`, find the block after `ai.clear_history()` and before the greeting playback begins. Replace that section with:

```python
    ai.clear_history()
    if ai_local:
        ai_local.clear_history()

    # Submit vision analysis to run concurrently with the greeting clip
    _scene_future = None
    if _VISION_AVAILABLE:
        _scene_future = _vision_executor.submit(_vision.analyse_scene)

    # Play greeting (vision runs in background during this)
    if cfg.silent_wakeword and cfg.led_listening_enabled:
        log.info("Silent wake word mode — skipping audio greeting")
        session_log.log_turn("(wake word)", "GREETING", None, "silent",
                     response_text="(silent — LED only)")
    else:
        greeting_resp = _greeting_handler.handle("(wake word)", "GREETING")
        if greeting_resp:
            leds.set_talking()
            audio.play(greeting_resp.wav_path, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
            session_log.log_turn("(wake word)", "GREETING", None, greeting_resp.method,
                         response_text=os.path.basename(greeting_resp.wav_path))
        else:
            text = "Yo. What do you want?"
            wav = tts_generate.speak(text)
            try:
                leds.set_talking()
                audio.play(wav, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
            finally:
                os.unlink(wav)
            session_log.log_turn("(wake word)", "GREETING", None, "pre_gen_tts", response_text=text)

    # Collect vision result and inject into LLM context
    if _scene_future is not None:
        try:
            _scene = _scene_future.result(timeout=5.0)
            _ctx = _scene.to_context_string()
            ai.inject_scene_context(_ctx)
            if ai_local:
                ai_local.inject_scene_context(_ctx)
            log.info("Scene context injected: %s", _ctx)
        except Exception as e:
            log.warning("Vision analysis failed: %s", e)
```

- [ ] **Step 5: Smoke test by restarting bender-converse**

```bash
ssh pi@192.168.68.132 'sudo systemctl restart bender-converse && sleep 3 && sudo systemctl status bender-converse --no-pager | tail -5'
```

Expected: `active (running)` — no crash on startup.

- [ ] **Step 6: Commit**

```bash
cd /home/pi/bender
git add scripts/wake_converse.py
git commit -m "feat: inject scene context at session start concurrently with greeting"
```

---

## Task 8: Passive Mode Watcher

**Files:**
- Modify: `scripts/wake_converse.py`

- [ ] **Step 1: Add session_active helper**

In `scripts/wake_converse.py`, after `_vision_executor`, add a helper function that checks whether a session is currently active (the session file is used for this elsewhere in the codebase):

```python
def _is_session_active() -> bool:
    """Returns True if a conversation session is currently running."""
    return os.path.exists(cfg.session_file)
```

- [ ] **Step 2: Add passive vision callback**

In `scripts/wake_converse.py`, add the callback that the VisionWatcher calls when it detects a scene. This plays the commentary through the same audio path as normal responses:

```python
def _on_passive_scene(scene) -> None:
    """Callback from VisionWatcher — speak a Bender comment about the scene."""
    from handlers.vision_handler import VisionHandler
    handler = VisionHandler()
    resp = handler.handle_passive(scene)
    if resp is None:
        return
    log.info("Passive vision comment: %s", resp.text)
    _passive_log = SessionLogger()
    _passive_log.session_start()
    audio.open_session()
    try:
        leds.set_talking()
        audio.play(resp.wav_path, on_chunk=_check_abort_on_chunk, on_done=leds.all_off)
        _passive_log.log_turn(
            "(passive vision scan)", "VISION_PASSIVE", None,
            "handler_vision", response_text=resp.text,
        )
        _passive_log.session_end("passive_vision")
    finally:
        audio.close_session()
        if resp.is_temp and resp.wav_path and os.path.exists(resp.wav_path):
            os.unlink(resp.wav_path)
```

- [ ] **Step 3: Start VisionWatcher in main()**

In `scripts/wake_converse.py`, find `main()`. After the line that starts `briefings.refresh_all` thread, add:

```python
    # Start passive vision watcher if enabled
    if _VISION_AVAILABLE:
        _watcher = _vision.VisionWatcher(
            on_scene=_on_passive_scene,
            session_active_fn=_is_session_active,
            config=cfg,
        )
        _watcher.start()
        log.info("Vision watcher started (passive_enabled=%s)", cfg.vision_passive_enabled)
```

- [ ] **Step 4: Smoke test**

```bash
ssh pi@192.168.68.132 'sudo systemctl restart bender-converse && sleep 3 && sudo journalctl -u bender-converse -n 10 --no-pager'
```

Expected: Log shows `Vision watcher started` with no errors.

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender
git add scripts/wake_converse.py
git commit -m "feat: add passive vision watcher daemon thread"
```

---

## Task 9: API Endpoints

**Files:**
- Modify: `scripts/web/app.py`
- Create: `tests/test_web_vision.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_web_vision.py`:

```python
"""Tests for vision API endpoints."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with patch.dict('sys.modules', {
        'cv2': MagicMock(),
        'picamera2': MagicMock(),
        'pvporcupine': MagicMock(),
        'pyaudio': MagicMock(),
        'spidev': MagicMock(),
    }):
        from web.app import app
        return TestClient(app)


PIN = "2904"
HEADERS = {"X-Bender-Pin": PIN}


def test_get_passive_status_disabled(client):
    resp = client.get("/api/vision/passive", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["minutes_remaining"] is None


def test_post_passive_enable_30m(client):
    resp = client.post("/api/vision/passive", json={"duration_minutes": 30}, headers=HEADERS)
    assert resp.status_code == 200
    # Verify it's now enabled
    status = client.get("/api/vision/passive", headers=HEADERS).json()
    assert status["enabled"] is True
    assert status["minutes_remaining"] is not None
    assert status["minutes_remaining"] <= 30


def test_post_passive_enable_indefinite(client):
    resp = client.post("/api/vision/passive", json={"duration_minutes": None}, headers=HEADERS)
    assert resp.status_code == 200
    status = client.get("/api/vision/passive", headers=HEADERS).json()
    assert status["enabled"] is True
    assert status["minutes_remaining"] is None


def test_delete_passive_disables(client):
    client.post("/api/vision/passive", json={"duration_minutes": 30}, headers=HEADERS)
    resp = client.delete("/api/vision/passive", headers=HEADERS)
    assert resp.status_code == 200
    status = client.get("/api/vision/passive", headers=HEADERS).json()
    assert status["enabled"] is False


def test_vision_analyse_returns_text(client):
    mock_scene = MagicMock()
    mock_scene.to_context_string.return_value = "adult male ~35"
    mock_scene.is_empty.return_value = False
    mock_scene.faces = [MagicMock(age_estimate=35, gender="male", confidence=0.9)]

    with patch('web.app.analyse_scene', return_value=mock_scene), \
         patch('web.app.tts_generate'):
        resp = client.post("/api/vision/analyse", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "text" in data
        assert "faces" in data


def test_vision_endpoints_require_pin(client):
    resp = client.get("/api/vision/passive")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_web_vision.py -v 2>&1 | head -20
```

Expected: FAIL — routes don't exist yet.

- [ ] **Step 3: Add vision endpoints to web/app.py**

In `scripts/web/app.py`, add these imports near the top (after existing imports):

```python
try:
    from vision import analyse_scene as _analyse_scene
    _VISION_AVAILABLE = True
except Exception:
    _VISION_AVAILABLE = False
```

Add the 4 endpoints (place them in a new section `# ── Vision Endpoints` after the existing Camera Stream section):

```python
# ── Vision Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/vision/passive", dependencies=[Depends(require_pin)])
async def vision_passive_status():
    """Get current passive mode status."""
    enabled = cfg.vision_passive_enabled
    expires_at = cfg.vision_passive_expires_at
    minutes_remaining = None
    if enabled and expires_at:
        from datetime import datetime, timezone
        try:
            expires = datetime.fromisoformat(expires_at)
            delta = expires - datetime.now(timezone.utc)
            minutes_remaining = max(0, int(delta.total_seconds() / 60))
        except ValueError:
            pass
    return {"enabled": enabled, "expires_at": expires_at, "minutes_remaining": minutes_remaining}


@app.post("/api/vision/passive", dependencies=[Depends(require_pin)])
async def vision_passive_enable(body: dict = Body(...)):
    """Enable passive mode. duration_minutes=null for indefinite."""
    duration = body.get("duration_minutes")
    cfg.vision_passive_enabled = True
    if duration is not None:
        from datetime import datetime, timezone, timedelta
        expires = datetime.now(timezone.utc) + timedelta(minutes=int(duration))
        cfg.vision_passive_expires_at = expires.isoformat()
    else:
        cfg.vision_passive_expires_at = ""
    await asyncio.to_thread(cfg.save)
    return {"ok": True}


@app.delete("/api/vision/passive", dependencies=[Depends(require_pin)])
async def vision_passive_disable():
    """Disable passive mode immediately."""
    cfg.vision_passive_enabled = False
    cfg.vision_passive_expires_at = ""
    await asyncio.to_thread(cfg.save)
    return {"ok": True}


@app.post("/api/vision/analyse", dependencies=[Depends(require_pin)])
async def vision_analyse():
    """Trigger on-demand scene analysis. Returns commentary text + face data."""
    if not _VISION_AVAILABLE:
        raise HTTPException(status_code=503, detail="Vision not available")

    scene = await asyncio.to_thread(_analyse_scene)
    faces = [
        {"age_estimate": f.age_estimate, "gender": f.gender, "confidence": f.confidence}
        for f in scene.faces
    ]

    # Generate commentary (reuse VisionHandler logic)
    from handlers.vision_handler import VisionHandler
    handler = VisionHandler()
    resp = handler.handle_passive(scene)
    text = resp.text if resp else scene.to_context_string()

    # Speak it on the device (best-effort, don't block response)
    if resp and resp.wav_path:
        async def _play():
            import audio as _audio
            _audio.open_session()
            try:
                import leds as _leds
                _leds.set_talking()
                _audio.play(resp.wav_path, on_done=_leds.all_off)
            finally:
                _audio.close_session()
                if resp.is_temp and os.path.exists(resp.wav_path):
                    os.unlink(resp.wav_path)
        asyncio.create_task(_play())

    return {"text": text, "faces": faces}
```

- [ ] **Step 4: Run tests**

```bash
cd /home/pi/bender
source venv/bin/activate
pytest tests/test_web_vision.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender
git add scripts/web/app.py tests/test_web_vision.py
git commit -m "feat: add vision API endpoints (passive toggle + on-demand analyse)"
```

---

## Task 10: Frontend — Config Passive Mode UI

**Files:**
- Modify: `web/src/pages/Config.svelte`
- Modify: `web/src/lib/api.js`

- [ ] **Step 1: Add API client methods to api.js**

In `web/src/lib/api.js`, add these functions (alongside existing API functions):

```javascript
export async function visionPassiveGet(pin) {
  const r = await fetch('/api/vision/passive', { headers: { 'X-Bender-Pin': pin } });
  if (!r.ok) throw new Error('Failed to get passive status');
  return r.json();
}

export async function visionPassiveSet(pin, durationMinutes) {
  const r = await fetch('/api/vision/passive', {
    method: 'POST',
    headers: { 'X-Bender-Pin': pin, 'Content-Type': 'application/json' },
    body: JSON.stringify({ duration_minutes: durationMinutes }),
  });
  if (!r.ok) throw new Error('Failed to enable passive mode');
  return r.json();
}

export async function visionPassiveClear(pin) {
  const r = await fetch('/api/vision/passive', {
    method: 'DELETE',
    headers: { 'X-Bender-Pin': pin },
  });
  if (!r.ok) throw new Error('Failed to disable passive mode');
  return r.json();
}
```

- [ ] **Step 2: Add passive mode section to Config.svelte**

In `web/src/pages/Config.svelte`, add the Vision section. Find where config sections end (near `</form>` or the last section), and add a new section before it:

In the `<script>` block, add:

```javascript
import { onMount } from 'svelte';
import { pin } from '../lib/stores/session.js';  // follow existing pattern in other pages
import { visionPassiveGet, visionPassiveSet, visionPassiveClear } from '../lib/api.js';

let visionPassiveEnabled = false;
let visionPassiveMinutesRemaining = null;
let visionPassiveLoading = false;

const VISION_PRESETS = [
  { label: '15m', minutes: 15 },
  { label: '30m', minutes: 30 },
  { label: '1h', minutes: 60 },
  { label: '3h', minutes: 180 },
  { label: '∞', minutes: null },
];

async function loadVisionPassive() {
  try {
    const data = await visionPassiveGet($pin);
    visionPassiveEnabled = data.enabled;
    visionPassiveMinutesRemaining = data.minutes_remaining;
  } catch (e) { /* non-critical */ }
}

async function setVisionPassive(minutes) {
  visionPassiveLoading = true;
  try {
    await visionPassiveSet($pin, minutes);
    await loadVisionPassive();
  } finally {
    visionPassiveLoading = false;
  }
}

async function clearVisionPassive() {
  visionPassiveLoading = true;
  try {
    await visionPassiveClear($pin);
    visionPassiveEnabled = false;
    visionPassiveMinutesRemaining = null;
  } finally {
    visionPassiveLoading = false;
  }
}

onMount(() => {
  loadVisionPassive();
  const interval = setInterval(loadVisionPassive, 60000);
  return () => clearInterval(interval);
});
```

In the template, add the Vision section:

```svelte
<section class="config-section">
  <h3>Vision</h3>
  <div class="passive-row">
    <label>Passive mode</label>
    <div class="passive-controls">
      <button
        class="toggle-btn"
        class:active={visionPassiveEnabled}
        on:click={() => visionPassiveEnabled ? clearVisionPassive() : setVisionPassive(null)}
        disabled={visionPassiveLoading}
      >
        {visionPassiveEnabled ? 'ON' : 'OFF'}
      </button>
      {#each VISION_PRESETS as preset}
        <button
          class="preset-btn"
          on:click={() => setVisionPassive(preset.minutes)}
          disabled={visionPassiveLoading}
        >
          {preset.label}
        </button>
      {/each}
    </div>
  </div>
  {#if visionPassiveEnabled}
    <p class="passive-status">
      {visionPassiveMinutesRemaining !== null
        ? `Time remaining: ${visionPassiveMinutesRemaining} minutes`
        : 'Active indefinitely'}
    </p>
  {/if}
</section>
```

- [ ] **Step 3: Build the frontend**

```bash
ssh pi@192.168.68.132 'cd /home/pi/bender/web && npm run build 2>&1 | tail -5'
```

Expected: Build success, no errors.

- [ ] **Step 4: Verify in browser**

Open `http://192.168.68.132:8080` → Config page. The Vision section should appear with OFF toggle and preset buttons. Clicking a preset should show the timer countdown after ~1s.

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender
git add web/src/pages/Config.svelte web/src/lib/api.js web/dist/
git commit -m "feat: add passive mode UI to Config page with preset timer buttons"
```

---

## Task 11: Frontend — Puppet "Ask Bender" Button

**Files:**
- Modify: `web/src/pages/Puppet.svelte`
- Modify: `web/src/lib/api.js`

- [ ] **Step 1: Add visionAnalyse to api.js**

In `web/src/lib/api.js`, add:

```javascript
export async function visionAnalyse(pin) {
  const r = await fetch('/api/vision/analyse', {
    method: 'POST',
    headers: { 'X-Bender-Pin': pin },
  });
  if (!r.ok) throw new Error('Vision analyse failed');
  return r.json();
}
```

- [ ] **Step 2: Add "Ask Bender" button to Puppet.svelte**

In `web/src/pages/Puppet.svelte`, in the `<script>` block add:

```javascript
import { visionAnalyse } from '../lib/api.js';

let visionText = '';
let visionFaces = [];
let visionLoading = false;

async function askBender() {
  visionLoading = true;
  visionText = '';
  try {
    const data = await visionAnalyse($pin);
    visionText = data.text;
    visionFaces = data.faces;
  } catch (e) {
    visionText = 'Something went wrong. Typical.';
  } finally {
    visionLoading = false;
  }
}
```

In the template, find the camera stream section and add the Ask Bender button and output below it:

```svelte
<div class="vision-controls">
  <button class="ask-btn" on:click={askBender} disabled={visionLoading}>
    {visionLoading ? 'Analysing...' : 'Ask Bender what he sees'}
  </button>
  {#if visionText}
    <p class="vision-commentary">{visionText}</p>
    {#if visionFaces.length > 0}
      <p class="vision-faces">
        Detected: {visionFaces.map(f => `${f.gender} ~${f.age_estimate}`).join(', ')}
      </p>
    {/if}
  {/if}
</div>
```

- [ ] **Step 3: Build and verify**

```bash
ssh pi@192.168.68.132 'cd /home/pi/bender/web && npm run build 2>&1 | tail -5'
```

Open `http://192.168.68.132:8080` → Puppet page. "Ask Bender what he sees" button should appear below the camera stream. Clicking it should trigger vision analysis, show the commentary text, and Bender should speak on the device.

- [ ] **Step 4: Full test suite**

```bash
ssh pi@192.168.68.132 'cd /home/pi/bender && source venv/bin/activate && pytest tests/ -v --tb=short 2>&1 | tail -20'
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/pi/bender
git add web/src/pages/Puppet.svelte web/src/lib/api.js web/dist/
git commit -m "feat: add Ask Bender vision button to Puppet page"
```

---

## Task 12: End-to-End Verification

- [ ] **Step 1: Restart bender-converse**

```bash
ssh pi@192.168.68.132 'sudo systemctl restart bender-converse && sleep 3 && sudo systemctl status bender-converse --no-pager | tail -8'
```

Expected: `active (running)`, log shows `Vision watcher started`.

- [ ] **Step 2: Test voice command**

Say "Hey Bender" → wait for greeting → say "what do you see". Bender should respond with a scene description in character.

Check logs:

```bash
ssh pi@192.168.68.132 'tail -5 /home/pi/bender/logs/$(date +%Y-%m-%d).jsonl | python3 -m json.tool'
```

Expected: A `turn` entry with `"intent": "VISION"`.

- [ ] **Step 3: Test session context injection**

Say "Hey Bender" → then ask something unrelated like "what time is it?". Bender's response should optionally reference who's in the room naturally.

Check logs to confirm a `session_start` entry is followed by a turn with `"method": "handler_vision"` or the context appearing in an AI response.

- [ ] **Step 4: Test UI passive toggle**

In Config UI, click "15m". Verify toggle shows ON and countdown appears. Wait 1 minute, verify countdown decrements. Click toggle to turn off.

- [ ] **Step 5: Test UI Ask Bender button**

In Puppet UI, click "Ask Bender what he sees". Verify commentary appears in UI and Bender speaks on device.

- [ ] **Step 6: Final commit tag**

```bash
ssh pi@192.168.68.132 'cd /home/pi/bender && git log --oneline -8'
```

Review the commit history looks clean, then optionally tag:

```bash
ssh pi@192.168.68.132 'cd /home/pi/bender && git tag vision-approach-a'
```
