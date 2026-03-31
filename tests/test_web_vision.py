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
    return {"audio": mock_audio, "leds": mock_leds, "vision": mock_vision, "tts_generate": mock_tts}


def test_vision_passive_enable():
    with patch.dict(sys.modules, _base_mocks()):
        client = get_client()
        resp = client.post("/api/vision/passive", json={"duration_minutes": 30}, headers=auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["expires_at"] != ""


def test_vision_passive_enable_indefinite():
    with patch.dict(sys.modules, _base_mocks()):
        client = get_client()
        resp = client.post("/api/vision/passive", json={"duration_minutes": None}, headers=auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["expires_at"] == ""


def test_vision_passive_disable():
    with patch.dict(sys.modules, _base_mocks()):
        client = get_client()
        # Enable first
        client.post("/api/vision/passive", json={"duration_minutes": 10}, headers=auth())
        resp = client.delete("/api/vision/passive", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_vision_passive_status():
    with patch.dict(sys.modules, _base_mocks()):
        client = get_client()
        client.post("/api/vision/passive", json={"duration_minutes": 60}, headers=auth())
        resp = client.get("/api/vision/passive", headers=auth())
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "expires_at" in data
    assert "minutes_remaining" in data


def test_vision_passive_requires_pin():
    with patch.dict(sys.modules, _base_mocks()):
        client = get_client()
        resp = client.get("/api/vision/passive")
    assert resp.status_code == 401


def test_vision_analyse_empty():
    from vision import SceneDescription

    mock_scene = MagicMock(spec=SceneDescription)
    mock_scene.is_empty.return_value = True
    mock_scene.faces = []

    mocks = _base_mocks()
    mocks["vision"].analyse_scene = lambda: mock_scene
    mocks["vision"].SceneDescription = SceneDescription

    with patch.dict(sys.modules, mocks):
        client = get_client()
        with patch("os.path.exists", return_value=False):
            resp = client.post("/api/vision/analyse", headers=auth())

    assert resp.status_code == 200
    data = resp.json()
    assert "I don't see anyone" in data["text"]
    assert data["faces"] == []


def test_vision_analyse_with_faces():
    from vision import SceneDescription, FaceInfo

    face = MagicMock(spec=FaceInfo)
    face.age_estimate = 35
    face.gender = "male"
    face.confidence = 0.92

    mock_scene = MagicMock(spec=SceneDescription)
    mock_scene.is_empty.return_value = False
    mock_scene.faces = [face]
    mock_scene.to_context_string.return_value = "[Room: 1 person]"

    mocks = _base_mocks()
    mocks["vision"].analyse_scene = lambda: mock_scene
    mocks["vision"].SceneDescription = SceneDescription
    mocks["vision"].FaceInfo = FaceInfo

    with patch.dict(sys.modules, mocks):
        client = get_client()
        with patch("os.path.exists", return_value=False):
            resp = client.post("/api/vision/analyse", headers=auth())

    assert resp.status_code == 200
    data = resp.json()
    assert "I can see" in data["text"]
    assert len(data["faces"]) == 1
    assert data["faces"][0]["age"] == 35
    assert data["faces"][0]["gender"] == "male"
