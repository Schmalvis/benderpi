import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch, MagicMock
import pytest


def test_vision_handler_empty_room():
    """Returns a Response when no faces detected."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription
    from datetime import datetime

    empty_scene = SceneDescription(faces=[], captured_at=datetime.now(), raw_detections={})

    with patch("vision.analyse_scene", return_value=empty_scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("what do you see", "VISION")

    assert resp is not None
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"


def test_vision_handler_with_faces():
    """Returns a Response describing detected faces."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription, FaceInfo
    from datetime import datetime

    scene = SceneDescription(
        faces=[FaceInfo(age_estimate=35, gender="male", confidence=0.9, bbox=(0, 0, 100, 100))],
        captured_at=datetime.now(),
        raw_detections={},
    )

    with patch("vision.analyse_scene", return_value=scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("who's in the room", "VISION")

    assert resp is not None
    assert resp.wav_path == "/tmp/test.wav"
    assert "35" in resp.text or "adult" in resp.text or "male" in resp.text


def test_vision_handler_multiple_faces():
    """Response correctly joins multiple faces with 'and'."""
    from handlers.vision_handler import VisionHandler
    from vision import SceneDescription, FaceInfo
    from datetime import datetime

    scene = SceneDescription(
        faces=[
            FaceInfo(age_estimate=35, gender="male", confidence=0.9, bbox=(0, 0, 100, 100)),
            FaceInfo(age_estimate=8, gender="female", confidence=0.85, bbox=(100, 0, 200, 100)),
        ],
        captured_at=datetime.now(),
        raw_detections={},
    )

    with patch("vision.analyse_scene", return_value=scene), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        handler = VisionHandler()
        resp = handler.handle("describe the room", "VISION")

    assert resp is not None
    assert "and" in resp.text
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
