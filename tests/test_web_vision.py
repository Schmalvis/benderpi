"""Tests for vision API endpoints."""
import os
import sys
import types
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"


def get_client(extra_modules=None):
    os.environ["BENDER_WEB_PIN"] = PIN
    import importlib
    import web.app
    importlib.reload(web.app)
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def auth():
    return {"X-Bender-Pin": PIN}


def _base_mocks():
    """Return sys.modules patches needed to suppress hardware imports."""
    mock_audio = types.ModuleType("audio")
    mock_audio.play = lambda wav, on_done=None: None
    mock_leds = types.ModuleType("leds")
    mock_leds.set_talking = lambda: None
    mock_leds.all_off = lambda: None
    mock_vision = types.ModuleType("vision")
    mock_tts = types.ModuleType("tts_generate")
    mock_tts.speak = lambda text: "/tmp/test.wav"
    import types as _t
    mock_ai = _t.ModuleType("ai_response")
    class _MockAIResponder:
        def respond(self, prompt): return "Mock Bender response."
    mock_ai.AIResponder = _MockAIResponder
    return {"audio": mock_audio, "leds": mock_leds, "vision": mock_vision, "tts_generate": mock_tts, "ai_response": mock_ai}




def test_vision_analyse_empty():
    from vision import SceneDescription

    mock_scene = MagicMock()
    mock_scene.is_empty.return_value = True
    mock_scene.to_context_string.return_value = ""
    mock_scene.objects = []

    mocks = _base_mocks()
    mocks["vision"].analyse_scene = lambda: mock_scene
    mocks["vision"].SceneDescription = SceneDescription

    with patch.dict(sys.modules, mocks):
        client = get_client()
        with patch("os.path.exists", return_value=False):
            resp = client.post("/api/vision/analyse", headers=auth())

    assert resp.status_code == 200
    data = resp.json()
    assert "text" in data
    assert data["objects"] == []


def test_vision_analyse_with_description():
    """VLM-based scene description flows through the endpoint correctly."""
    from vision import SceneDescription

    mock_scene = MagicMock()
    mock_scene.is_empty.return_value = False
    mock_scene.to_context_string.return_value = "A person is reading a book on the sofa."
    mock_scene.objects = []

    mocks = _base_mocks()
    mocks["vision"].analyse_scene = lambda: mock_scene
    mocks["vision"].SceneDescription = SceneDescription

    with patch.dict(sys.modules, mocks):
        client = get_client()
        with patch("os.path.exists", return_value=False):
            resp = client.post("/api/vision/analyse", headers=auth())

    assert resp.status_code == 200
    data = resp.json()
    assert "text" in data
    assert data["text"] == "Mock Bender response."
