"""vlm.py — Hailo VLM lifecycle wrapper for BenderPi.

Owns VLM initialisation (lazy, once per process) and exposes a single
public function: describe_scene(frame, prompt, timeout).

Frame contract: caller passes an RGB uint8 numpy array (as produced by
camera.py).  This module resizes to 336×336 before inference.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread pool — single worker so Hailo calls are serialised
# ---------------------------------------------------------------------------

_executor = ThreadPoolExecutor(max_workers=1)

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------

_vdevice = None
_vlm = None

_DEFAULT_PROMPT = "Briefly describe what you see."
_SYSTEM_PROMPT = (
    "You are a vision assistant. "
    "Describe what you see briefly and factually."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _default_timeout() -> float:
    return float(os.environ.get("BENDER_VLM_TIMEOUT", "4.0"))


def _ensure_init() -> None:
    """Initialise VDevice and VLM on first call; no-op thereafter."""
    global _vdevice, _vlm
    if _vlm is not None:
        return

    from hailo_platform import VDevice
    from hailo_platform.genai import VLM
    from hailo_apps.python.core.common.defines import (
        SHARED_VDEVICE_GROUP_ID,
        VLM_CHAT_APP,
        HAILO10H_ARCH,
    )
    from hailo_apps.python.core.common.core import resolve_hef_path

    log.info("Initialising Hailo VLM (lazy, first call)")
    params = VDevice.create_params()
    params.group_id = SHARED_VDEVICE_GROUP_ID
    _vdevice = VDevice(params)

    hef_path = resolve_hef_path(None, app_name=VLM_CHAT_APP, arch=HAILO10H_ARCH)
    _vlm = VLM(_vdevice, str(hef_path))
    log.info("Hailo VLM ready — HEF: %s", hef_path)


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


def _preprocess(frame: np.ndarray) -> np.ndarray:
    """Resize to 336×336 and enforce uint8."""
    resized = cv2.resize(frame, (336, 336), interpolation=cv2.INTER_LINEAR)
    return resized.astype(np.uint8)


def _run_inference(frame_resized: np.ndarray, user_prompt: str) -> str:
    """Run VLM inference synchronously (called from thread pool)."""
    _ensure_init()
    prompt = _build_prompt(user_prompt)
    response = _vlm.generate_all(
        prompt=prompt,
        frames=[frame_resized],
        temperature=0.1,
        seed=42,
        max_generated_tokens=200,
    )
    response = response.split("<|im_end|>")[0].strip()
    _vlm.clear_context()
    return response


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def describe_scene(
    frame: np.ndarray,
    prompt: str | None = None,
    timeout: float | None = None,
) -> str:
    """Describe the scene in *frame* using the Hailo VLM.

    Args:
        frame:   RGB uint8 numpy array from camera.py.
        prompt:  Optional override for the user prompt.
        timeout: Max seconds to wait for inference.  Defaults to
                 ``BENDER_VLM_TIMEOUT`` env var (4.0 s).

    Returns:
        Model description string, or ``""`` on timeout.
    """
    user_prompt = prompt or _DEFAULT_PROMPT
    effective_timeout = timeout if timeout is not None else _default_timeout()

    frame_resized = _preprocess(frame)

    future = _executor.submit(_run_inference, frame_resized, user_prompt)
    try:
        return future.result(timeout=effective_timeout)
    except FuturesTimeoutError:
        log.warning("VLM inference timed out after %ss", effective_timeout)
        return ""
    except Exception:
        log.exception("VLM inference error")
        return ""
