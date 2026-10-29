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


# ── Camera status carries whether scene analysis is switched on ─────────────
#
# A live camera stream and a working scene analysis are separate things. The
# MJPEG feed needs only Picamera2. "Ask Bender" needs the Hailo KV-Cache that
# the resident conversation LLM holds, so on this device it always 503s.
# Reporting `vision_enabled` lets the page hide that button instead of offering
# a click that costs a ~23s bender-converse restart and returns an error.

def test_camera_status_reports_vision_enabled(monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "vlm_enabled", True)
    client = get_client()
    with patch("web.routes.puppet._check_camera", return_value=True):
        resp = client.get("/api/puppet/camera/status", headers=auth())
    assert resp.status_code == 200
    assert resp.json() == {"available": True, "vision_enabled": True}


def test_camera_status_reports_vision_disabled(monkeypatch):
    """The shipped state: bender_config.json has vlm_enabled false."""
    from config import cfg
    monkeypatch.setattr(cfg, "vlm_enabled", False)
    client = get_client()
    with patch("web.routes.puppet._check_camera", return_value=True):
        resp = client.get("/api/puppet/camera/status", headers=auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True          # the stream still works
    assert body["vision_enabled"] is False    # the description does not


# ---------------------------------------------------------------------------
# Hear Room (/ws/puppet/mic)
#
# 2026-10-29: the stream held the service guard for its whole life. The page
# fell quiet after a second or two without closing the socket, so the server
# streamed the full 120 s cap with bender-converse STOPPED, and every puppet
# speak, Puppet Mode press and Hear Room retry in those two minutes was
# refused. The mic is an ALSA dsnoop, so the stream now shares it instead.
# ---------------------------------------------------------------------------

class _FakeStdout:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        import asyncio
        await asyncio.sleep(0)
        return self._chunks.pop(0) if self._chunks else b""


class _FakeProc:
    def __init__(self, chunks):
        self.stdout = _FakeStdout(chunks)
        self.returncode = None

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


def _mic_url():
    from web.auth import issue_stream_token
    return f"/ws/puppet/mic?token={issue_stream_token()}"


def _fake_arecord(chunks):
    async def _exec(*args, **kwargs):
        _fake_arecord.args = args
        return _FakeProc(chunks)
    return _exec


def test_hear_room_does_not_stop_bender():
    client = get_client()
    import web.routes.puppet as puppet
    with patch.object(puppet, "service_lease", side_effect=AssertionError("must not take the guard")), \
         patch("subprocess.run", side_effect=AssertionError("must not call systemctl")), \
         patch("asyncio.create_subprocess_exec", _fake_arecord([b"\x01\x00" * 8])):
        with client.websocket_connect(_mic_url()) as ws:
            assert ws.receive_bytes() == b"\x01\x00" * 8
    assert "default" in _fake_arecord.args


def test_hear_room_never_splits_a_sample():
    client = get_client()
    with patch("asyncio.create_subprocess_exec", _fake_arecord([b"\x01\x02\x03", b"\x04\x05"])):
        with client.websocket_connect(_mic_url()) as ws:
            first = ws.receive_bytes()
            second = ws.receive_bytes()
    assert first == b"\x01\x02"
    assert second == b"\x03\x04"
    assert len(first) % 2 == 0 and len(second) % 2 == 0


def test_a_newer_hear_room_stream_replaces_the_old_one():
    import web.routes.puppet as puppet
    client = get_client()
    endless = [b"\x00\x00"] * 10_000
    with patch("asyncio.create_subprocess_exec", _fake_arecord(endless)):
        with client.websocket_connect(_mic_url()) as old:
            old.receive_bytes()
            puppet._mic_generation += 1          # what a second press does
            from starlette.websockets import WebSocketDisconnect
            import pytest
            with pytest.raises(WebSocketDisconnect):
                for _ in range(10_000):
                    old.receive_bytes()


def test_volume_uses_the_card_name_not_a_number():
    client = get_client()
    mock_result = type("R", (), {"returncode": 0, "stdout": "  Mono: Playback 120 [94%] [0.00dB] [on]"})()
    with patch("subprocess.run", return_value=mock_result) as run:
        assert client.get("/api/config/volume", headers=auth()).status_code == 200
        assert client.post("/api/config/volume", json={"level": 60}, headers=auth()).status_code == 200
    for call in run.call_args_list:
        cmd = call.args[0]
        assert cmd[:3] == ["amixer", "-c", "seeed2micvoicec"], cmd
