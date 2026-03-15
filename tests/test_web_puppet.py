"""Tests for puppet mode API."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"


def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    # Force reimport to pick up new env
    import importlib
    import web.app
    importlib.reload(web.app)
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def auth():
    return {"X-Bender-Pin": PIN}


def test_speak_returns_ok():
    import types
    # Create mock modules for hardware libs not available on dev machine
    mock_tts = types.ModuleType("tts_generate")
    mock_tts.speak = lambda text: "/tmp/test.wav"
    mock_audio = types.ModuleType("audio")
    mock_audio.play_oneshot = lambda path: None
    client = get_client()
    with patch.dict(sys.modules, {"tts_generate": mock_tts, "audio": mock_audio}), \
         patch("os.unlink"):
        resp = client.post("/api/puppet/speak", json={"text": "hello"}, headers=auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_speak_rejects_long_text():
    client = get_client()
    resp = client.post("/api/puppet/speak", json={"text": "x" * 501}, headers=auth())
    assert resp.status_code == 400


def test_speak_rejects_empty():
    client = get_client()
    resp = client.post("/api/puppet/speak", json={"text": ""}, headers=auth())
    assert resp.status_code == 400


def test_clips_returns_list():
    client = get_client()
    resp = client.get("/api/puppet/clips", headers=auth())
    assert resp.status_code == 200
    assert "clips" in resp.json()


def test_favourite_toggle():
    client = get_client()
    with patch("web.app._FAVOURITES_PATH", os.path.join(os.path.dirname(__file__), "_test_favs.json")):
        resp = client.post("/api/puppet/favourite",
                           json={"path": "speech/wav/hello.wav", "favourite": True},
                           headers=auth())
        assert resp.status_code == 200
    # Clean up
    try:
        os.unlink(os.path.join(os.path.dirname(__file__), "_test_favs.json"))
    except OSError:
        pass


def test_clip_rejects_path_traversal():
    client = get_client()
    resp = client.post("/api/puppet/clip",
                       json={"path": "../../etc/passwd"},
                       headers=auth())
    assert resp.status_code == 400


def test_clip_rejects_non_wav():
    client = get_client()
    resp = client.post("/api/puppet/clip",
                       json={"path": "speech/wav/hello.txt"},
                       headers=auth())
    assert resp.status_code == 400


def test_volume_get():
    client = get_client()
    mock_result = type("R", (), {"returncode": 0, "stdout": "  Front Left: Playback 50 [85%] [0.00dB] [on]"})()
    with patch("subprocess.run", return_value=mock_result):
        resp = client.get("/api/config/volume", headers=auth())
        assert resp.status_code == 200
        assert resp.json()["level"] == 85


def test_volume_set():
    client = get_client()
    mock_result = type("R", (), {"returncode": 0, "stdout": ""})()
    with patch("subprocess.run", return_value=mock_result):
        resp = client.post("/api/config/volume", json={"level": 75}, headers=auth())
        assert resp.status_code == 200
        assert resp.json()["level"] == 75
