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
    error: str = ""

    def is_empty(self) -> bool:
        """Return True when there is no meaningful description."""
        return not self.description.strip()

    def failed(self) -> bool:
        """Return True when the look never happened.

        Deliberately separate from `is_empty()`, and every caller must check
        this FIRST. "The camera did not look" is not "the room is empty", and
        until 2026-10-15 both arrived as an identical empty SceneDescription.
        Two callers then spoke the second: the web route prompted the AI with
        "detected nobody", and VisionHandler said "Either the room's empty, or
        you've finally achieved irrelevance." On this device the VLM load
        fails every time, because the resident conversation LLM holds the
        single on-chip KV-Cache, so every one of those claims was invented.
        """
        return bool(self.error)

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
    """Capture a frame and describe it via VLM. Returns SceneDescription.

    A failure sets `error` and leaves `description` empty, so the caller can
    tell a failed look from an empty room. See `SceneDescription.failed()`.
    """
    try:
        camera.acquire_camera()
        frame = camera.capture_frame()
        description = vlm.describe_scene(frame, prompt=cfg.vlm_prompt)
        if description is None:
            # The VLM timed out, or it could not load. On this device the load
            # fails every time while bender-converse holds the KV-Cache.
            log.warning("analyse_scene: the vision model returned no answer")
            return SceneDescription(error="the vision model did not answer")
        return SceneDescription(description=description, captured_at=datetime.now())
    except RuntimeError as e:
        if "busy" in str(e).lower():
            log.warning("Camera busy (held by another process) — vision skipped this session")
            return SceneDescription(error="the camera is held by another process")
        log.exception("analyse_scene failed (capture)")
        return SceneDescription(error=f"the camera failed: {e}")
    except Exception as exc:
        log.exception("analyse_scene failed")
        return SceneDescription(error=f"the camera failed: {exc}")
    finally:
        camera.release_camera()
