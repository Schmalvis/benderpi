"""Vision module for BenderPi.

Uses the Raspberry Pi AI Camera (IMX500) with YOLO11n on-device
inference for multi-class object detection. The camera is managed as a shared singleton;
both the MJPEG stream and analyse_scene() use the same Picamera2 instance.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

from config import cfg
from logger import get_logger

log = get_logger("vision")

# ---------------------------------------------------------------------------
# IMX500 model configuration
# ---------------------------------------------------------------------------

_OUT_BOXES   = 0
_OUT_SCORES  = 1
_OUT_CLASSES = 2
_OUT_COUNT   = 3

# COCO 80-class index -> label
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


def _coco_label(idx: int) -> "str | None":
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
