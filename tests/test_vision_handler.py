"""Tests for handlers/vision_handler.py — VLM-based vision handler.

All hardware interaction is mocked; these tests run offline.

The point of most of these is WHICH answer comes back, not that an answer
comes back. Until 2026-10-15 every test here asserted only `resp is not None`,
and all five still passed with the handler describing a room it had never
looked at. `_EYES_OFF` and `_EMPTY_ROOM_RESPONSES` are different claims:

  * `_EYES_OFF` says the camera does not work. Always honest.
  * `_EMPTY_ROOM_RESPONSES` say the room is empty. Only honest when the
    camera actually looked AND the model actually answered.

On the device the second was the guaranteed answer to "what can you see",
because `vlm.describe_scene` returned "" for a failed look and `analyse_scene`
flattened that into the same empty SceneDescription an empty room produces.
The VLM cannot load there at all while the conversation LLM holds the single
on-chip KV-Cache, so the camera never looked once.
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

_EYES_OFF = "My eyes aren't working right now. Probably a Tuesday thing."


@pytest.fixture
def vision_on(monkeypatch):
    """cfg.vlm_enabled true, restored afterwards.

    `cfg` is an import-time singleton and the shipped bender_config.json has
    `vlm_enabled: false`, so without this the handler short-circuits.
    """
    from config import cfg
    monkeypatch.setattr(cfg, "vlm_enabled", True)
    return cfg


def _scene(description="", error=""):
    from vision import SceneDescription
    return SceneDescription(description=description,
                            captured_at=datetime.now(), error=error)


# ── The room may only be described when the camera looked ───────────────────

def test_vlm_disabled_says_the_eyes_are_off_and_never_looks(monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "vlm_enabled", False)
    from handlers.vision_handler import VisionHandler

    with patch(_ANALYSE_SCENE) as mock_look, \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        resp = VisionHandler().handle("what do you see", "VISION")

    assert resp.text == _EYES_OFF
    mock_look.assert_not_called()


def test_a_failed_look_says_the_eyes_are_off_not_an_empty_room(vision_on):
    """The exact device case: the VLM could not load."""
    from handlers.vision_handler import VisionHandler
    from handlers.vision_handler import _EMPTY_ROOM_RESPONSES

    failed = _scene(error="the vision model did not answer")
    with patch(_ANALYSE_SCENE, return_value=failed), \
         patch("handlers.vision_handler._bender_scene_response") as mock_llm, \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        resp = VisionHandler().handle("what do you see", "VISION")

    assert resp.text == _EYES_OFF
    assert resp.text not in _EMPTY_ROOM_RESPONSES
    mock_llm.assert_not_called()


def test_vision_handler_empty_room(vision_on):
    """An empty answer after a real look may still claim an empty room."""
    from handlers.vision_handler import VisionHandler
    from handlers.vision_handler import _EMPTY_ROOM_RESPONSES

    with patch(_ANALYSE_SCENE, return_value=_scene(description="")), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        resp = VisionHandler().handle("what do you see", "VISION")

    assert resp.text in _EMPTY_ROOM_RESPONSES
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"


def test_vision_handler_camera_error(vision_on):
    """An exception out of analyse_scene is the same honest answer."""
    from handlers.vision_handler import VisionHandler

    with patch(_ANALYSE_SCENE, side_effect=RuntimeError("camera offline")), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        resp = VisionHandler().handle("look around", "VISION")

    assert resp.text == _EYES_OFF
    assert resp.method == "handler_vision"
    assert resp.intent == "VISION"


# ── A real description reaches the LLM verbatim ─────────────────────────────

def test_vision_handler_single_subject(vision_on):
    from handlers.vision_handler import VisionHandler

    scene = _scene(description="A person is sitting in a chair.")
    with patch(_ANALYSE_SCENE, return_value=scene), \
         patch("handlers.vision_handler._bender_scene_response",
               return_value="I see a meatbag.") as mock_llm, \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        resp = VisionHandler().handle("what do you see", "VISION")

    assert resp.text == "I see a meatbag."
    mock_llm.assert_called_once_with("A person is sitting in a chair.")


def test_vision_handler_with_description(vision_on):
    from handlers.vision_handler import VisionHandler

    scene = _scene(description="A person is sitting on a sofa reading a book.")
    with patch(_ANALYSE_SCENE, return_value=scene), \
         patch("handlers.vision_handler._bender_scene_response",
               return_value="A meatbag reading."), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        resp = VisionHandler().handle("who's in the room", "VISION")

    assert resp.text == "A meatbag reading."
    assert resp.wav_path == "/tmp/test.wav"


def test_vision_handler_multiple_subjects(vision_on):
    from handlers.vision_handler import VisionHandler

    scene = _scene(description=("Two people are sitting at a table. One is "
                                "using a laptop, the other is drinking coffee."))
    with patch(_ANALYSE_SCENE, return_value=scene), \
         patch("handlers.vision_handler._bender_scene_response",
               return_value="Two meatbags detected."), \
         patch("tts_generate.speak", return_value="/tmp/test.wav"):
        resp = VisionHandler().handle("describe the room", "VISION")

    assert resp.text == "Two meatbags detected."
    assert resp.is_temp is True
