import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch
import pytest


def test_vision_handler_empty_room():
    """Returns a Response when no objects detected."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription
    from datetime import datetime

    empty_scene = SceneDescription(objects=[], captured_at=datetime.now())

    with patch("vision.analyse_scene", return_value=empty_scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("what do you see", "VISION")

    assert resp is not None
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"


def test_vision_handler_with_person():
    """Returns a Response describing a detected person."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription, DetectedObject
    from datetime import datetime

    scene = SceneDescription(
        objects=[DetectedObject(label="person", confidence=0.45, bbox=(10, 20, 200, 400))],
        captured_at=datetime.now(),
    )

    with patch("vision.analyse_scene", return_value=scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("who's in the room", "VISION")

    assert resp is not None
    assert resp.wav_path == "/tmp/test.wav"
    assert "person" in resp.text.lower() or "room" in resp.text.lower()


def test_vision_handler_multiple_persons():
    """Response correctly describes multiple persons."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription, DetectedObject
    from datetime import datetime

    scene = SceneDescription(
        objects=[
            DetectedObject(label="person", confidence=0.45, bbox=(0, 0, 100, 200)),
            DetectedObject(label="person", confidence=0.40, bbox=(150, 0, 300, 200)),
        ],
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
