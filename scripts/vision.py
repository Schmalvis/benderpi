"""Vision module for BenderPi.

Uses the Raspberry Pi AI Camera (IMX500) with EfficientDet-Lite0 on-device
inference for person detection. The camera is managed as a shared singleton;
both the MJPEG stream and analyse_scene() use the same Picamera2 instance.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

from logger import get_logger

log = get_logger("vision")

# ---------------------------------------------------------------------------
# IMX500 model configuration
# ---------------------------------------------------------------------------
_IMX500_MODEL = "/usr/share/imx500-models/imx500_network_efficientdet_lite0_pp.rpk"

# EfficientDet-Lite0 COCO 90-class: person = 0 (0-indexed).
_COCO_PERSON_CLASS = 0
_PERSON_CONFIDENCE_THRESHOLD = 0.35

# Output tensor indices from get_outputs() for efficientdet_lite0_pp.
# Confirmed via diagnostic: boxes(1,100,4) | scores(1,100) | classes(1,100) | count(1,1)
# Boxes are in pixel coordinates (0-320 range), NOT normalised 0-1.
_OUT_BOXES = 0
_OUT_SCORES = 1
_OUT_CLASSES = 2

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

    log.info("Initialising IMX500 with model: %s", _IMX500_MODEL)
    imx500 = IMX500(_IMX500_MODEL)
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
                parts.append(f"{n} {label}{"s" if n != 1 else ""}")
        return "[Room: " + ", ".join(parts) + "]" 


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyse_scene() -> SceneDescription:
    """Return detected persons using IMX500 on-device inference.

    Acquires the shared camera (starting it if needed), reads inference
    metadata, then releases. Works whether or not the MJPEG stream is active.
    """
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
        # Retry a few times in case inference is still warming up
        np_outputs = None
        for attempt in range(5):
            metadata = cam.capture_metadata()
            np_outputs = imx500.get_outputs(metadata, add_batch=True)
            if np_outputs is not None:
                break
            import time as _time
            _time.sleep(0.2)

        if np_outputs is None:
            log.info("IMX500 inference not ready after warmup — returning empty scene")
            return SceneDescription()

        boxes = np_outputs[_OUT_BOXES][0]    # (100, 4) pixel coords
        scores = np_outputs[_OUT_SCORES][0]  # (100,)
        classes = np_outputs[_OUT_CLASSES][0]  # (100,)

        persons = []
        for box, score, cls in zip(boxes, scores, classes):
            if float(score) < _PERSON_CONFIDENCE_THRESHOLD:
                continue
            if int(round(float(cls))) != _COCO_PERSON_CLASS:
                continue
            persons.append(PersonInfo(
                confidence=float(score),
                bbox=tuple(float(v) for v in box),
            ))

        log.info("Vision: %d person(s) detected (top score=%.3f)",
                 len(persons), float(max(scores)) if len(scores) else 0.0)
        return SceneDescription(persons=persons, captured_at=datetime.now())
    finally:
        release_camera()
