"""Tests for handlers/vision_handler.py — VLM-based vision handler.

All hardware interaction is mocked; these tests run offline.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch
from datetime import datetime
import pytest

# Patch target: vision_handler imports `vision` at module level.
# After test_vision.py calls importlib.reload(vision), sys.modules["vision"]
# points to a new object but vision_handler still holds the old reference.
# Patching via the handler's own namespace ensures the right object is targeted.
_ANALYSE_SCENE = "handlers.vision_handler.vision.analyse_scene"


def test_vision_handler_empty_room():
    """Returns a Response when VLM returns empty description."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription

    empty_scene = SceneDescription(description="", captured_at=datetime.now())

    with patch(_ANALYSE_SCENE, return_value=empty_scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("what do you see", "VISION")

    assert resp is not None
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"


def test_vision_handler_single_subject():
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription
    scene = SceneDescription(description="A person is sitting in a chair.", captured_at=datetime.now())
    with patch(_ANALYSE_SCENE, return_value=scene), \
         patch("handlers.vision_handler._bender_scene_response", return_value="I see a meatbag.") as mock_llm, \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("what do you see", "VISION")
    assert resp is not None
    mock_llm.assert_called_once_with("A person is sitting in a chair.")


def test_vision_handler_with_description():
    """Returns a Response incorporating the VLM scene description."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription

    scene = SceneDescription(
        description="A person is sitting on a sofa reading a book.",
        captured_at=datetime.now(),
    )

    with patch(_ANALYSE_SCENE, return_value=scene), \
         patch("handlers.vision_handler._bender_scene_response", return_value="A meatbag reading."), \
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

    with patch(_ANALYSE_SCENE, return_value=scene), \
         patch("handlers.vision_handler._bender_scene_response", return_value="Two meatbags detected."), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("describe the room", "VISION")

    assert resp is not None
    assert resp.is_temp is True


def test_vision_handler_camera_error():
    """Gracefully handles analyse_scene exceptions."""
    from handlers.vision_handler import VisionHandler

    with patch(_ANALYSE_SCENE, side_effect=RuntimeError("camera offline")), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("look around", "VISION")

    assert resp is not None
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"
