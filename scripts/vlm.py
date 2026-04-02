"""scripts/vlm.py — YOLO object detection + Qwen2.5-1.5B LLM scene description.

Public API:
    describe_scene(frame, prompt=None, timeout=None) -> str
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import List

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_YOLO_HEF = "/usr/local/hailo/resources/models/hailo10h/yolov8m.hef"
_LLM_HEF = "/usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef"
_COCO_LABELS = "/home/pi/hailo-apps/local_resources/coco.txt"
_CONFIDENCE_THRESHOLD = 0.5
_DEFAULT_PROMPT = "Briefly describe what you see."
_SYSTEM_PROMPT = "You are a concise scene describer. Respond in one or two sentences."

# ---------------------------------------------------------------------------
# Thread pool — single worker so Hailo calls are serialised
# ---------------------------------------------------------------------------

_executor = ThreadPoolExecutor(max_workers=1)

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_yolo: object = None
_llm: object = None
_labels: List[str] = []
_init_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_timeout() -> float:
    return float(os.environ.get("BENDER_VLM_TIMEOUT", "30.0"))


def _load_labels(path: str) -> List[str]:
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as exc:
        log.warning("Could not load COCO labels from %s: %s", path, exc)
        return []


def _ensure_init() -> None:
    """Initialise HailoInfer (YOLO) and LLM on first call; no-op thereafter."""
    global _yolo, _llm, _labels
    with _init_lock:
        if _llm is not None:
            return

        from hailo_apps.python.core.common.hailo_inference import HailoInfer
        from hailo_platform.genai import LLM
        from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID

        log.info("Initialising YOLO HailoInfer (lazy, first call)")
        _yolo = HailoInfer(_YOLO_HEF)

        log.info("Initialising Qwen2.5-1.5B LLM (lazy, first call)")
        from hailo_platform import VDevice
        params = VDevice.create_params()
        params.group_id = SHARED_VDEVICE_GROUP_ID
        vdevice = VDevice(params)
        _llm = LLM(vdevice, _LLM_HEF)

        _labels = _load_labels(_COCO_LABELS)
        log.info("VLM pipeline ready — YOLO + LLM loaded, %d COCO labels", len(_labels))


def _run_yolo(frame: np.ndarray) -> List[str]:
    """Run YOLO inference synchronously via async callback bridged to threading.Event.

    Returns deduplicated, confidence-sorted list of strings like ["person (0.92)"].
    """
    input_shape = _yolo.get_input_shape()  # (H, W, C)
    h, w = input_shape[0], input_shape[1]
    resized = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.uint8)

    done_event = threading.Event()
    result_holder: List = []

    def _callback(completion_info=None, bindings_list=None):
        # HailoInfer.run() calls partial(fn, bindings_list=real_bindings) then
        # Hailo's callback_wrapper calls that partial with completion_info=<info>.
        # Tests call callback(mock_info, bindings_list=mock_list) (positional + kwarg).
        try:
            outputs = []
            for b in bindings_list:
                for name in _yolo.output_type:
                    outputs.append(b.output(name).get_buffer())
            result_holder.append(outputs)
        except Exception as exc:
            log.warning("YOLO callback error: %s", exc)
        finally:
            done_event.set()

    job = _yolo.run([resized], _callback)
    done_event.wait(timeout=15.0)
    if not done_event.is_set() and job is not None:
        job.wait(timeout_ms=15000)

    if not result_holder:
        log.warning("YOLO inference did not complete within timeout")
        return []

    # result_holder[0] = [nms_output] (one element per named output)
    # nms_output = list of 80 per-class detection lists
    nms_output = result_holder[0][0]
    log.debug("YOLO nms_output len=%d, first few: %s", len(nms_output), nms_output[:3])

    # YOLO NMS output: list indexed by class_id,
    # each element is a list of detections as [y_min, x_min, y_max, x_max, score]
    best: dict = {}  # label -> max score
    for class_id, dets in enumerate(nms_output):
        if dets is None:
            continue
        for det in dets:
            score = float(det[4]) if len(det) > 4 else 0.0
            if score < _CONFIDENCE_THRESHOLD:
                continue
            label = _labels[class_id] if class_id < len(_labels) else str(class_id)
            if label not in best or score > best[label]:
                best[label] = score

    sorted_dets = sorted(best.items(), key=lambda x: x[1], reverse=True)
    return [f"{label} ({score:.2f})" for label, score in sorted_dets]


def _build_llm_prompt(detected_objects: List[str], user_prompt: str) -> list:
    objects_str = ", ".join(detected_objects) if detected_objects else "nothing detected"
    user_text = f"Detected objects: {objects_str}. {user_prompt}"
    return [
        {"role": "system", "content": [{"type": "text", "text": _SYSTEM_PROMPT}]},
        {"role": "user", "content": [{"type": "text", "text": user_text}]},
    ]


def _run_inference(frame: np.ndarray, user_prompt: str) -> str:
    """Run YOLO then LLM, return description string."""
    _ensure_init()
    detected = _run_yolo(frame)
    log.info("YOLO detected: %s", detected or "nothing")
    messages = _build_llm_prompt(detected, user_prompt)
    try:
        response = _llm.generate_all(prompt=messages, temperature=0.1, seed=42, max_generated_tokens=150)
        response = response.split("<|im_end|>")[0]
        response = response.split(". [{'type'")[0]
        return response.strip()
    finally:
        _llm.clear_context()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def describe_scene(
    frame: np.ndarray,
    prompt: str | None = None,
    timeout: float | None = None,
) -> str:
    """Describe a scene using YOLO object detection + Qwen2.5-1.5B LLM.

    Args:
        frame: BGR image as numpy array.
        prompt: Optional question / instruction. Defaults to a general description prompt.
        timeout: Max seconds to wait. Falls back to BENDER_VLM_TIMEOUT env var (default 30s).

    Returns:
        Description string, or "" on timeout/error.
    """
    user_prompt = prompt or _DEFAULT_PROMPT
    effective_timeout = timeout if timeout is not None else _default_timeout()

    future = _executor.submit(_run_inference, frame, user_prompt)
    try:
        return future.result(timeout=effective_timeout)
    except FuturesTimeoutError:
        log.warning("describe_scene timed out after %.1fs", effective_timeout)
        future.cancel()
        return ""
    except Exception as exc:
        log.warning("describe_scene failed: %s", exc)
        return ""
