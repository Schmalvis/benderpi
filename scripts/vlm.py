"""scripts/vlm.py — Qwen2-VL-2B vision-language model scene description.

Public API:
    describe_scene(frame, prompt=None, timeout=None) -> str
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VLM_HEF = "/usr/local/hailo/resources/models/hailo10h/Qwen2-VL-2B-Instruct.hef"
_IMAGE_SIZE = 336  # Qwen2-VL expects 336x336 RGB
_DEFAULT_PROMPT = "Briefly describe what you see."
_SYSTEM_PROMPT = "You are a concise scene describer. Respond in one or two sentences."

# ---------------------------------------------------------------------------
# Thread pool — single worker so Hailo calls are serialised
# ---------------------------------------------------------------------------

_executor = ThreadPoolExecutor(max_workers=1)

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_vlm: object = None
_vdevice: object = None
_init_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_timeout() -> float:
    return float(os.environ.get("BENDER_VLM_TIMEOUT", "60.0"))


def _ensure_init() -> None:
    """Initialise VLM on first call; no-op thereafter."""
    global _vlm, _vdevice
    with _init_lock:
        if _vlm is not None:
            return

        from hailo_platform import VDevice
        from hailo_platform.genai import VLM
        from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID

        log.info("Initialising Qwen2-VL-2B VLM (lazy, first call)")
        params = VDevice.create_params()
        params.group_id = SHARED_VDEVICE_GROUP_ID
        _vdevice = VDevice(params)
        _vlm = VLM(_vdevice, _VLM_HEF)
        log.info("VLM ready — Qwen2-VL-2B loaded")


def release() -> None:
    """Release VLM and Hailo VDevice to free KV-Cache for the LLM."""
    global _vlm, _vdevice
    _vlm = None
    _vdevice = None
    log.info("VLM released — Hailo KV-Cache freed")


def _preprocess(frame: np.ndarray) -> np.ndarray:
    """Resize frame to 336x336 RGB uint8. Input is already RGB from Picamera2 (RGB888)."""
    resized = cv2.resize(frame, (_IMAGE_SIZE, _IMAGE_SIZE), interpolation=cv2.INTER_LINEAR)
    return resized.astype(np.uint8)


def _build_prompt(user_prompt: str) -> list:
    return [
        {
            "role": "system",
            "content": [{"type": "text", "text": _SYSTEM_PROMPT}],
        },
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": user_prompt},
            ],
        },
    ]


def _run_inference(frame: np.ndarray, user_prompt: str) -> str:
    """Run VLM inference with the actual image, return description string."""
    _ensure_init()
    image = _preprocess(frame)
    messages = _build_prompt(user_prompt)
    try:
        response = _vlm.generate_all(
            prompt=messages,
            frames=[image],
            temperature=0.1,
            seed=42,
            max_generated_tokens=150,
        )
        response = response.split("<|im_end|>")[0]
        response = response.split(". [{'type'")[0]
        return response.strip()
    finally:
        _vlm.clear_context()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def describe_scene(
    frame: np.ndarray,
    prompt: str | None = None,
    timeout: float | None = None,
) -> str:
    """Describe a scene using Qwen2-VL-2B vision-language model.

    Args:
        frame: RGB image as numpy array.
        prompt: Optional question / instruction. Defaults to a general description prompt.
        timeout: Max seconds to wait. Falls back to BENDER_VLM_TIMEOUT env var (default 60s).

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
