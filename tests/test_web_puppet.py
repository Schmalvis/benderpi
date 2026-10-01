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
    from web.auth import issue_token
    return {"X-Bender-Token": issue_token()}


def test_speak_returns_ok():
    import types
    mock_tts = types.ModuleType("tts_generate")
    mock_tts.speak = lambda text: "/tmp/test.wav"
    mock_audio = types.ModuleType("audio")
    mock_audio.play_oneshot = lambda path, *args: None
    mock_leds = types.ModuleType("leds")
    mock_leds.set_talking = lambda: None
    mock_leds.set_level = lambda level: None
    mock_leds.all_off = lambda: None
    client = get_client()
    with patch.dict(sys.modules, {"tts_generate": mock_tts}), \
         patch("web.routes.puppet.audio", mock_audio), \
         patch("web.routes.puppet.leds", mock_leds), \
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
    with patch("web.routes.puppet._FAVOURITES_PATH", os.path.join(os.path.dirname(__file__), "_test_favs.json")):
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


# ---------------------------------------------------------------------------
# The "say this" IPC route
#
# Measured on-device 2026-10-01: the old route stopped bender-converse, played,
# and started it again -- 3.5s of silence before a soundboard clip, 23s of
# deafness afterwards, and a start-limit kill on the SIXTH action in five
# minutes. Now the request goes to the process that already owns the speaker,
# and the direct route survives only for when that process is DOWN.
# ---------------------------------------------------------------------------

def _say_mocks(tmp_wav="/tmp/test.wav"):
    import types
    mock_tts = types.ModuleType("tts_generate")
    mock_tts.speak = lambda text: tmp_wav
    mock_audio = types.ModuleType("audio")
    mock_audio.play_oneshot = lambda path, *args: None
    mock_leds = types.ModuleType("leds")
    mock_leds.set_talking = lambda: None
    mock_leds.set_level = lambda level: None
    mock_leds.all_off = lambda: None
    return mock_tts, mock_audio, mock_leds


def test_speak_uses_the_ipc_route_when_converse_is_running():
    """No systemctl, no model reload, no start limit."""
    mock_tts, mock_audio, mock_leds = _say_mocks()
    requested = {}
    client = get_client()
    with patch.dict(sys.modules, {"tts_generate": mock_tts}), \
         patch("web.routes.puppet.audio", mock_audio), \
         patch("web.routes.puppet.leds", mock_leds), \
         patch("web.routes.puppet.converse_is_running", lambda: True), \
         patch("web.routes.puppet._move_to_say_temp", lambda p: "/repo/.say_tmp/x.wav"), \
         patch("web.routes.puppet.say_request.sweep_temp", lambda *a, **k: 0), \
         patch("web.routes.puppet.say_request.request",
               lambda wav, **kw: requested.update(wav=wav, **kw) or "abc123"), \
         patch("web.routes.puppet._play_guarded") as direct:
        resp = client.post("/api/puppet/speak", json={"text": "hello"}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["route"] == "ipc"
    assert resp.json()["id"] == "abc123"
    assert requested["temp"] is True, "a one-off render must be marked temp"
    direct.assert_not_called(), "the service must not be stopped any more"


def test_speak_falls_back_to_the_direct_route_when_converse_is_down():
    """Puppet-only mode: nobody owns the speaker, so take it."""
    mock_tts, mock_audio, mock_leds = _say_mocks()
    client = get_client()
    with patch.dict(sys.modules, {"tts_generate": mock_tts}), \
         patch("web.routes.puppet.audio", mock_audio), \
         patch("web.routes.puppet.leds", mock_leds), \
         patch("web.routes.puppet.converse_is_running", lambda: False), \
         patch("web.routes.puppet._move_to_say_temp", lambda p: p), \
         patch("web.routes.puppet.say_request.sweep_temp", lambda *a, **k: 0), \
         patch("web.routes.puppet.say_request.request") as ipc, \
         patch("web.routes.puppet._play_guarded") as direct, \
         patch("os.unlink"):
        resp = client.post("/api/puppet/speak", json={"text": "hello"}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["route"] == "direct"
    ipc.assert_not_called()
    direct.assert_called_once()


def test_the_ipc_route_does_not_delete_the_render():
    """The conversation process plays it LATER. Deleting it on return would
    hand Bender a missing file -- silence at the door."""
    mock_tts, mock_audio, mock_leds = _say_mocks()
    client = get_client()
    with patch.dict(sys.modules, {"tts_generate": mock_tts}), \
         patch("web.routes.puppet.audio", mock_audio), \
         patch("web.routes.puppet.leds", mock_leds), \
         patch("web.routes.puppet.converse_is_running", lambda: True), \
         patch("web.routes.puppet._move_to_say_temp", lambda p: "/repo/.say_tmp/x.wav"), \
         patch("web.routes.puppet.say_request.sweep_temp", lambda *a, **k: 0), \
         patch("web.routes.puppet.say_request.request", lambda wav, **kw: "id1"), \
         patch("os.unlink") as unlink:
        resp = client.post("/api/puppet/speak", json={"text": "hi"}, headers=auth())
    assert resp.status_code == 200
    unlink.assert_not_called()


def test_the_direct_route_still_deletes_the_render():
    """It played it inline, so it owns the cleanup."""
    mock_tts, mock_audio, mock_leds = _say_mocks()
    client = get_client()
    with patch.dict(sys.modules, {"tts_generate": mock_tts}), \
         patch("web.routes.puppet.audio", mock_audio), \
         patch("web.routes.puppet.leds", mock_leds), \
         patch("web.routes.puppet.converse_is_running", lambda: False), \
         patch("web.routes.puppet._move_to_say_temp", lambda p: p), \
         patch("web.routes.puppet.say_request.sweep_temp", lambda *a, **k: 0), \
         patch("web.routes.puppet._play_guarded", lambda p: None), \
         patch("os.unlink") as unlink:
        resp = client.post("/api/puppet/speak", json={"text": "hi"}, headers=auth())
    assert resp.status_code == 200
    unlink.assert_called()


def test_a_soundboard_clip_uses_the_ipc_route_too():
    client = get_client()
    requested = {}
    with patch("web.routes.puppet.converse_is_running", lambda: True), \
         patch("web.routes.puppet.say_request.request",
               lambda wav, **kw: requested.update(wav=wav, **kw) or "cid"), \
         patch("web.routes.puppet._play_guarded") as direct, \
         patch("os.path.isfile", lambda p: True):
        resp = client.post("/api/puppet/clip",
                           json={"path": "speech/wav/yo.wav"}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["route"] == "ipc"
    assert requested.get("temp") is not True, "a library clip must not be deleted"
    direct.assert_not_called()


def test_a_refused_request_falls_back_rather_than_going_silent():
    """If say_request rejects the path, the operator must still hear something
    (or get a real error) -- never a silent 200."""
    client = get_client()
    with patch("web.routes.puppet.converse_is_running", lambda: True), \
         patch("web.routes.puppet.say_request.request", lambda wav, **kw: None), \
         patch("web.routes.puppet._play_guarded") as direct, \
         patch("os.path.isfile", lambda p: True):
        resp = client.post("/api/puppet/clip",
                           json={"path": "speech/wav/yo.wav"}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["route"] == "direct"
    direct.assert_called_once()
