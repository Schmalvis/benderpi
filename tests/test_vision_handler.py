"""Tests for handlers/vision_handler.py — VLM-based vision handler.

All hardware interaction is mocked; these tests run offline.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch
from datetime import datetime
import pytest


def test_vision_handler_empty_room():
    """Returns a Response when VLM returns empty description."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription

    empty_scene = SceneDescription(description="", captured_at=datetime.now())

    with patch("vision.analyse_scene", return_value=empty_scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("what do you see", "VISION")

    assert resp is not None
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"


def test_vision_handler_with_description():
    """Returns a Response incorporating the VLM scene description."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription

    scene = SceneDescription(
        description="A person is sitting on a sofa reading a book.",
        captured_at=datetime.now(),
    )

    with patch("vision.analyse_scene", return_value=scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("who's in the room", "VISION")

    assert resp is not None
    assert resp.wav_path == "/tmp/test.wav"


def test_vision_handler_multiple_subjects():
    """Response handles a rich multi-subject VLM description."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription

    scene = SceneDescription(
        description="Two people are sitting at a table. One is using a laptop, the other is drinking coffee.",
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
