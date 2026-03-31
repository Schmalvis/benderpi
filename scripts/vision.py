"""Vision module for BenderPi.

Captures a frame from the camera and detects faces with age/gender estimation
using OpenCV DNN (CPU-based, no Hailo/IMX500 required).
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from logger import get_logger

log = get_logger("vision")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(_SCRIPT_DIR)
_MODELS_DIR = os.path.join(BASE_DIR, "models", "vision")
_FACE_PROTO = os.path.join(_MODELS_DIR, "deploy.prototxt")
_FACE_MODEL = os.path.join(_MODELS_DIR, "res10_300x300_ssd_iter_140000.caffemodel")
_AGE_PROTO = os.path.join(_MODELS_DIR, "age_deploy.prototxt")
_AGE_MODEL = os.path.join(_MODELS_DIR, "age_net.caffemodel")
_GENDER_PROTO = os.path.join(_MODELS_DIR, "gender_deploy.prototxt")
_GENDER_MODEL = os.path.join(_MODELS_DIR, "gender_net.caffemodel")

_MODEL_FILES = [
    _FACE_PROTO, _FACE_MODEL,
    _AGE_PROTO, _AGE_MODEL,
    _GENDER_PROTO, _GENDER_MODEL,
]

_MODEL_URLS = {
    _FACE_PROTO: "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt",
    _FACE_MODEL: "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel",
    _AGE_PROTO: "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/age_deploy.prototxt",
    _AGE_MODEL: "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/age_net.caffemodel",
    _GENDER_PROTO: "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/gender_deploy.prototxt",
    _GENDER_MODEL: "https://raw.githubusercontent.com/spmallick/learnopencv/master/AgeGender/gender_net.caffemodel",
}

# ---------------------------------------------------------------------------
# Age / gender labels
# ---------------------------------------------------------------------------
AGE_LIST = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']
GENDER_LIST = ['male', 'female']
AGE_MAP = {0: 1, 1: 5, 2: 10, 3: 17, 4: 28, 5: 40, 6: 50, 7: 75}

# ---------------------------------------------------------------------------
# Lazy-loaded net objects
# ---------------------------------------------------------------------------
_face_net = None
_age_net = None
_gender_net = None
_nets_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class FaceInfo:
    age_estimate: int       # e.g. 35
    gender: str             # "male" / "female"
    confidence: float       # 0.0–1.0
    bbox: tuple             # (x, y, w, h) — stored for future tracking


@dataclass
class SceneDescription:
    faces: list = field(default_factory=list)
    captured_at: datetime = field(default_factory=datetime.now)
    raw_detections: dict = field(default_factory=dict)

    def to_context_string(self) -> str:
        """Returns e.g. '[Room: adult male ~35, child female ~8]' or '[Room: empty]'."""
        if not self.faces:
            return "[Room: empty]"
        parts = []
        for face in self.faces:
            age = face.age_estimate
            if age < 13:
                label = "child"
            elif age < 18:
                label = "teen"
            else:
                label = "adult"
            parts.append(f"{label} {face.gender} ~{age}")
        return f"[Room: {', '.join(parts)}]"

    def is_empty(self) -> bool:
        """True if no faces detected."""
        return len(self.faces) == 0


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------
def models_available() -> bool:
    """Return True if all 6 model files exist on disk."""
    return all(os.path.exists(p) for p in _MODEL_FILES)


def ensure_models() -> None:
    """Download any missing model files. Logs progress."""
    import urllib.request

    os.makedirs(_MODELS_DIR, exist_ok=True)
    for path, url in _MODEL_URLS.items():
        if os.path.exists(path):
            continue
        fname = os.path.basename(path)
        log.info("Downloading model file: %s", fname)
        try:
            urllib.request.urlretrieve(url, path)
            log.info("Downloaded: %s", fname)
        except Exception as exc:
            log.error("Failed to download %s: %s", fname, exc)
            raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _load_nets():
    """Lazily load all DNN nets (once per process)."""
    import cv2  # noqa: import-outside-toplevel — guarded

    global _face_net, _age_net, _gender_net
    with _nets_lock:
        if _face_net is None:
            _face_net = cv2.dnn.readNet(_FACE_MODEL, _FACE_PROTO)
        if _age_net is None:
            _age_net = cv2.dnn.readNet(_AGE_MODEL, _AGE_PROTO)
        if _gender_net is None:
            _gender_net = cv2.dnn.readNet(_GENDER_MODEL, _GENDER_PROTO)


def _detect_faces(frame_bgr, net, conf_threshold: float = 0.5):
    """Run face detection and return list of (confidence, x1, y1, x2, y2)."""
    import cv2  # noqa

    h, w = frame_bgr.shape[:2]
    blob = cv2.dnn.blobFromImage(frame_bgr, 1.0, (300, 300), (104, 177, 123))
    net.setInput(blob)
    detections = net.forward()  # shape (1, 1, N, 7)

    faces = []
    for i in range(detections.shape[2]):
        confidence = float(detections[0, 0, i, 2])
        if confidence < conf_threshold:
            continue
        x1 = max(0, int(detections[0, 0, i, 3] * w))
        y1 = max(0, int(detections[0, 0, i, 4] * h))
        x2 = min(w, int(detections[0, 0, i, 5] * w))
        y2 = min(h, int(detections[0, 0, i, 6] * h))
        faces.append((confidence, x1, y1, x2, y2))
    return faces


def _estimate_age_gender(face_crop, age_net, gender_net):
    """Return (age_int, gender_str) for a single face crop."""
    import cv2  # noqa

    blob = cv2.dnn.blobFromImage(
        face_crop, 1.0, (227, 227), (78.4263377603, 87.7689143744, 114.895847746),
        swapRB=False
    )

    age_net.setInput(blob)
    age_preds = age_net.forward()
    age_idx = int(age_preds[0].argmax())
    age = AGE_MAP.get(age_idx)
    if age is None:
        log.warning("Unexpected age index %d from model, using 25", age_idx)
        age = 25

    gender_net.setInput(blob)
    gender_preds = gender_net.forward()
    gender_idx = int(gender_preds[0].argmax())
    if gender_idx >= len(GENDER_LIST):
        log.warning("Unexpected gender index %d from model", gender_idx)
        gender = "unknown"
    else:
        gender = GENDER_LIST[gender_idx]

    return age, gender


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyse_scene() -> SceneDescription:
    """Capture one frame and return a SceneDescription with face detections."""
    import cv2  # noqa

    if not models_available():
        log.warning("Vision models not available — returning empty SceneDescription")
        return SceneDescription()

    try:
        _load_nets()
    except Exception as exc:
        log.error("Failed to load DNN nets: %s", exc)
        return SceneDescription()

    # Capture frame
    frame_rgb = None
    try:
        from picamera2 import Picamera2  # noqa: import-outside-toplevel

        cam = Picamera2()
        config = cam.create_still_configuration(
            main={"size": (640, 480), "format": "RGB888"}
        )
        cam.configure(config)
        cam.start()
        try:
            frame_rgb = cam.capture_array()
        finally:
            cam.stop()
            cam.close()
    except Exception as exc:
        log.error("Camera capture failed: %s", exc)
        return SceneDescription()

    # Convert RGB → BGR for OpenCV
    import numpy as np  # noqa
    frame_bgr = frame_rgb[:, :, ::-1].copy()

    captured_at = datetime.now()

    # Detect faces
    try:
        raw_faces = _detect_faces(frame_bgr, _face_net)
    except Exception as exc:
        log.error("Face detection failed: %s", exc)
        return SceneDescription(captured_at=captured_at)

    face_infos = []
    for confidence, x1, y1, x2, y2 in raw_faces:
        face_crop = frame_bgr[y1:y2, x1:x2]
        if face_crop.size == 0:
            continue
        try:
            age, gender = _estimate_age_gender(face_crop, _age_net, _gender_net)
        except Exception as exc:
            log.warning("Age/gender estimation failed for face: %s", exc)
            continue
        face_infos.append(FaceInfo(
            age_estimate=age,
            gender=gender,
            confidence=confidence,
            bbox=(x1, y1, x2 - x1, y2 - y1),
        ))

    return SceneDescription(faces=face_infos, captured_at=captured_at)
