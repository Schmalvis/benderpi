"""vision.py — Scene analysis orchestrator for BenderPi.

Thin public API that composes camera.py (Picamera2 singleton) and
vlm.py (Hailo VLM inference) into a single callable surface.

Public API
----------
acquire_camera()        Delegate to camera.acquire_camera()
release_camera()        Delegate to camera.release_camera()
analyse_scene()         Capture a frame and describe it; returns SceneDescription
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import camera
import vlm
from logger import get_logger
from config import cfg

log = get_logger("vision")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SceneDescription:
    description: str = ""
    captured_at: datetime | None = None

    def is_empty(self) -> bool:
        """Return True when there is no meaningful description."""
        return not self.description.strip()

    def to_context_string(self) -> str:
        """Return formatted string for LLM context injection."""
        if self.is_empty():
            return ""
        return self.description.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def acquire_camera():
    """Delegate to camera.acquire_camera(). Returns Picamera2 instance."""
    return camera.acquire_camera()


def release_camera():
    """Delegate to camera.release_camera()."""
    camera.release_camera()


def analyse_scene() -> SceneDescription:
    """Capture a frame and describe it via VLM. Returns SceneDescription."""
    try:
        camera.acquire_camera()
        frame = camera.capture_frame()
        description = vlm.describe_scene(frame, prompt=cfg.vlm_prompt)
        return SceneDescription(description=description, captured_at=datetime.now())
    except Exception:
        log.exception("analyse_scene failed")
        return SceneDescription()
    finally:
        camera.release_camera()
