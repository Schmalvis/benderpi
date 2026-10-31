"""Tests for puppet mode API."""
import os
import re
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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


class TestVolumeSeam:
    """The page and the route must use the same key.

    `api.js` sent `{volume}`, the route wants `{level}`, and it answered every
    slider move with 400 for months. The GET had the same mismatch, so the
    slider always showed 80. The page swallowed both errors and each side's own
    tests passed, so this reads the keys out of the frontend source and uses
    them against the real route. Found live on 2026-10-31.
    """

    @staticmethod
    def _source(*parts):
        root = os.path.join(os.path.dirname(__file__), "..", "web", "src")
        with open(os.path.join(root, *parts), encoding="utf-8") as f:
            return f.read()

    def test_the_key_the_page_sends_is_the_key_the_route_reads(self):
        match = re.search(r"setVolume = .*?JSON\.stringify\(\{ (\w+) \}\)",
                          self._source("lib", "api.js"))
        assert match, "setVolume no longer has the shape this test reads"
        ok = type("R", (), {"returncode": 0, "stdout": ""})()
        with patch("subprocess.run", return_value=ok):
            resp = get_client().post("/api/config/volume",
                                     json={match.group(1): 75},
                                     headers=auth())
        assert resp.status_code == 200

    @pytest.mark.parametrize("parts, name", [
        (("lib", "components", "VolumeSlider.svelte"), "data"),
        (("pages", "Config.svelte"), "v"),
    ])
    def test_the_key_each_slider_reads_is_the_key_the_route_sends(
            self, parts, name):
        match = re.search(rf"\b{name}\.(\w+) \?\? null", self._source(*parts))
        assert match, f"{parts[-1]} no longer has the shape this test reads"
        out = type("R", (), {"returncode": 0,
                             "stdout": "  Front Left: Playback 114 [90%]"})()
        with patch("subprocess.run", return_value=out):
            body = get_client().get("/api/config/volume", headers=auth()).json()
        assert body[match.group(1)] == 90


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
# See Room (/api/puppet/camera/stream) and the camera check
#
# See Room shares one ref-counted camera with _check_camera, and with the turn
# frames worker later. cancel_all_and_flush() is global, so a direct
# cam.capture_array here could lose its job and block with no timeout. Every
# capture goes through camera.capture_frame: one job at a time, under one lock,
# always bounded. The route must also never hold a stale ref or turn a busy
# camera into a 500.
# ---------------------------------------------------------------------------

class _Busy(RuntimeError):
    """Stands in for camera.CameraBusy in the fake vision module."""


def _a_frame():
    import numpy as np
    return np.zeros((4, 4, 3), dtype=np.uint8)


def _fake_vision(captures=None):
    """A vision stand-in. `captures` are return values or exceptions, in order.
    Its Picamera2 object refuses capture_array, so a direct call fails the test."""
    cam = MagicMock()
    cam.capture_array.side_effect = AssertionError("the route called capture_array")
    return SimpleNamespace(
        CameraBusy=_Busy,
        acquire_camera=MagicMock(return_value=cam),
        release_camera=MagicMock(),
        capture_frame=MagicMock(side_effect=captures),
    )


def _get_stream(client):
    from web.auth import issue_stream_token
    return client.get(f"/api/puppet/camera/stream?token={issue_stream_token()}")


def _stream_with(fake, client=None):
    client = client or get_client()
    with patch("web.routes.puppet._vision", fake), \
         patch("web.routes.puppet._check_camera", return_value=True):
        return _get_stream(client)


def _real_camera(monkeypatch, cam):
    """A private copy of scripts/camera.py around a fake Picamera2 object.

    A private copy, so the suite's other reloads of `camera` cannot reach it.
    """
    import importlib.util
    picamera2 = types.ModuleType("picamera2")
    picamera2.Picamera2 = MagicMock(return_value=cam)
    monkeypatch.setitem(sys.modules, "picamera2", picamera2)
    spec = importlib.util.spec_from_file_location(
        "camera_for_puppet_tests",
        os.path.join(os.path.dirname(__file__), "..", "scripts", "camera.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _vision_over(camera_module):
    return SimpleNamespace(
        CameraBusy=camera_module.CameraBusy,
        acquire_camera=camera_module.acquire_camera,
        release_camera=camera_module.release_camera,
        capture_frame=camera_module.capture_frame,
    )


@pytest.fixture
def fresh_camera_cache(monkeypatch):
    monkeypatch.setattr("web.routes.puppet._camera_available_cache", None)


class TestSeeRoomStream:
    def test_it_captures_through_capture_frame(self):
        fake = _fake_vision([_a_frame(), TimeoutError("no frame")])

        resp = _stream_with(fake)

        assert resp.status_code == 200
        assert resp.content.count(b"--frame") == 1
        assert fake.capture_frame.call_count == 2

    def test_a_capture_timeout_ends_the_stream_and_releases(self):
        fake = _fake_vision([_a_frame(), _a_frame(), TimeoutError("no frame")])

        resp = _stream_with(fake)

        assert resp.status_code == 200
        assert resp.content.count(b"--frame") == 2
        fake.release_camera.assert_called_once()

    def test_a_timeout_is_logged(self, caplog):
        import logging
        fake = _fake_vision([TimeoutError("no frame in 3.0 s")])

        with caplog.at_level(logging.WARNING, logger="bender.puppet"):
            _stream_with(fake)

        assert any("no frame in 3.0 s" in r.message for r in caplog.records)

    def test_a_busy_capture_ends_the_stream_and_releases(self):
        fake = _fake_vision([_a_frame(), _Busy("lock held")])

        resp = _stream_with(fake)

        assert resp.status_code == 200
        assert resp.content.count(b"--frame") == 1
        fake.release_camera.assert_called_once()

    def test_a_busy_acquire_ends_the_stream_with_no_release(self):
        """It runs after the 200 headers, so all it can do is end cleanly. It
        took no ref, so a release would take someone else's."""
        fake = _fake_vision([_a_frame()])
        fake.acquire_camera.side_effect = _Busy("lock held")

        resp = _stream_with(fake)

        assert resp.status_code == 200
        assert resp.content == b""
        fake.capture_frame.assert_not_called()
        fake.release_camera.assert_not_called()

    def test_a_busy_release_does_not_escape(self):
        """TestClient re-raises a server error, so an escape fails this test."""
        fake = _fake_vision([_a_frame(), TimeoutError("no frame")])
        fake.release_camera.side_effect = _Busy("lock held")

        resp = _stream_with(fake)

        assert resp.status_code == 200

    def test_a_busy_release_is_an_error_that_names_the_cost(self, caplog):
        """The stream ends either way, but the ref stays counted, so the close
        that recovers a timed-out camera never runs. That is not a warning."""
        import logging
        fake = _fake_vision([_a_frame(), TimeoutError("no frame")])
        fake.release_camera.side_effect = _Busy("lock held")

        with caplog.at_level(logging.WARNING, logger="bender.puppet"):
            _stream_with(fake)

        errors = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "leaked" in errors[0]
        assert "bender-web restarts" in errors[0]

    def test_a_busy_release_is_not_retried(self):
        fake = _fake_vision([_a_frame(), TimeoutError("no frame")])
        fake.release_camera.side_effect = _Busy("lock held")

        _stream_with(fake)

        fake.release_camera.assert_called_once()

    def test_another_capture_failure_still_releases(self):
        fake = _fake_vision([RuntimeError("the sensor said no")])

        with pytest.raises(RuntimeError, match="the sensor said no"):
            _stream_with(fake)

        fake.release_camera.assert_called_once()

    def test_the_wall_clock_cap_still_ends_the_stream(self, monkeypatch):
        from config import cfg
        monkeypatch.setattr(cfg, "web_stream_max_s", 0.05, raising=False)
        fake = _fake_vision(lambda: _a_frame())

        resp = _stream_with(fake)

        assert resp.status_code == 200
        assert resp.content.count(b"--frame") >= 1
        fake.release_camera.assert_called_once()

    def test_a_cancel_during_a_capture_ends_it_and_releases(self):
        """The disconnect path. The browser left while a capture ran."""
        import asyncio
        import threading
        from web.auth import issue_stream_token
        from web.routes import puppet

        unblock = threading.Event()
        inside = threading.Event()

        def capture():
            inside.set()
            unblock.wait(5)
            return _a_frame()

        fake = _fake_vision(capture)

        async def drive():
            with patch("web.routes.puppet._check_camera", return_value=True):
                response = await puppet.puppet_camera_stream(token=issue_stream_token())
            stream = response.body_iterator
            task = asyncio.ensure_future(stream.__anext__())
            assert await asyncio.to_thread(inside.wait, 5), "no capture started"
            task.cancel()
            try:
                with pytest.raises(StopAsyncIteration):
                    await task
            finally:
                unblock.set()

        with patch("web.routes.puppet._vision", fake):
            asyncio.run(drive())

        fake.release_camera.assert_called_once()

    def test_the_real_recovery_path_through_camera_py(self, monkeypatch):
        """The real camera.py, with only Picamera2 faked. The second frame
        never arrives. The stream ends, the camera closes, nothing calls stop()."""
        import concurrent.futures

        cam = MagicMock()
        cam.capture_array.side_effect = [
            _a_frame(),
            concurrent.futures.TimeoutError(),
        ]
        camera_module = _real_camera(monkeypatch, cam)

        resp = _stream_with(_vision_over(camera_module))

        assert resp.status_code == 200
        assert resp.content.count(b"--frame") == 1
        for call in cam.capture_array.call_args_list:
            assert call.kwargs["wait"] == camera_module._CAPTURE_TIMEOUT_S
        cam.cancel_all_and_flush.assert_called()
        cam.close.assert_called_once()
        cam.stop.assert_not_called()
        assert camera_module._cam is None
        assert camera_module._cam_refcount == 0


class TestDisconnectDuringAcquire:
    """The browser leaves while the acquire thread runs. That cancel cannot stop
    the thread, so the thread still takes its ref, and only a finished thread
    can say whether it did. A leaked ref means the camera never closes.

    These run the real StreamingResponse on an ASGI 2.3 scope, which is what
    uvicorn sends. That takes Starlette's anyio path, where the cancel is
    level-triggered: it comes back at every await until the scope exits.
    """

    @staticmethod
    async def _disconnect_while_acquiring(inside):
        import asyncio
        from web.auth import issue_stream_token
        from web.routes import puppet

        async def receive():
            await asyncio.to_thread(inside.wait, 5)
            return {"type": "http.disconnect"}

        async def send(message):
            return None

        with patch("web.routes.puppet._check_camera", return_value=True):
            response = await puppet.puppet_camera_stream(token=issue_stream_token())
        await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)

    def test_the_ref_is_released_once_the_acquire_finishes(self, monkeypatch):
        """The real camera.py. Only the Picamera2 constructor blocks."""
        import asyncio
        import threading
        import time
        from web.routes import puppet

        inside, unblock = threading.Event(), threading.Event()
        cam = MagicMock()
        camera_module = _real_camera(monkeypatch, cam)

        def slow_open():
            inside.set()
            unblock.wait(5)
            return cam

        sys.modules["picamera2"].Picamera2.side_effect = slow_open

        async def drive():
            await self._disconnect_while_acquiring(inside)
            unblock.set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                # Not the refcount alone: it reads 0 before the thread takes its ref.
                if cam.close.called and camera_module._cam is None:
                    return
                await asyncio.sleep(0.01)

        with patch.object(puppet, "_vision", _vision_over(camera_module)):
            asyncio.run(drive())

        assert camera_module._cam_refcount == 0
        assert camera_module._cam is None
        cam.close.assert_called_once()

    @pytest.mark.parametrize("error", [_Busy("lock held"), RuntimeError("no camera")],
                             ids=["busy", "failed"])
    def test_a_failed_acquire_releases_nothing(self, error):
        """That acquire took no ref. A release would take another holder's."""
        import asyncio
        import threading
        from web.routes import puppet

        inside, unblock, finished = (threading.Event() for _ in range(3))

        def slow_failure():
            inside.set()
            unblock.wait(5)
            finished.set()
            raise error

        fake = _fake_vision()
        fake.acquire_camera.side_effect = slow_failure

        async def drive():
            await self._disconnect_while_acquiring(inside)
            unblock.set()
            assert await asyncio.to_thread(finished.wait, 5)
            await asyncio.sleep(0.05)      # room for a wrong release to show up

        with patch.object(puppet, "_vision", fake):
            asyncio.run(drive())

        fake.release_camera.assert_not_called()

    def test_a_busy_release_after_the_disconnect_is_logged(self, caplog):
        """The release runs on its own thread, so nothing else would see it."""
        import asyncio
        import logging
        import threading
        import time
        from web.routes import puppet

        inside, unblock, released = (threading.Event() for _ in range(3))

        def slow_open():
            inside.set()
            unblock.wait(5)
            return MagicMock()

        def busy_release():
            released.set()
            raise _Busy("lock held")

        fake = _fake_vision()
        fake.acquire_camera.side_effect = slow_open
        fake.release_camera.side_effect = busy_release

        async def drive():
            await self._disconnect_while_acquiring(inside)
            unblock.set()
            assert await asyncio.to_thread(released.wait, 5)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not any(
                    r.levelno == logging.ERROR for r in caplog.records):
                await asyncio.sleep(0.01)

        with caplog.at_level(logging.WARNING, logger="bender.puppet"), \
                patch.object(puppet, "_vision", fake):
            asyncio.run(drive())

        assert any(r.levelno == logging.ERROR and "leaked" in r.message
                   for r in caplog.records)


class TestCheckCamera:
    def test_a_good_check_acquires_and_releases(self, fresh_camera_cache):
        from web.routes import puppet
        fake = _fake_vision()

        with patch.object(puppet, "_vision", fake):
            assert puppet._check_camera() is True

        fake.acquire_camera.assert_called_once()
        fake.release_camera.assert_called_once()

    def test_busy_returns_false_and_releases_nothing(self, fresh_camera_cache):
        from web.routes import puppet
        fake = _fake_vision()
        fake.acquire_camera.side_effect = _Busy("lock held")

        with patch.object(puppet, "_vision", fake):
            assert puppet._check_camera() is False

        fake.release_camera.assert_not_called()

    def test_busy_is_not_cached(self, fresh_camera_cache):
        """Busy is about this moment, not the camera. A 10 s cached False would
        return 503 from See Room for 10 s after the lock came free."""
        from web.routes import puppet
        fake = _fake_vision()
        fake.acquire_camera.side_effect = [_Busy("lock held"), MagicMock()]

        with patch.object(puppet, "_vision", fake):
            assert puppet._check_camera() is False
            assert puppet._check_camera() is True

    def test_a_real_failure_is_still_cached(self, fresh_camera_cache):
        """A missing camera stays missing. Do not retry an open on every call."""
        from web.routes import puppet
        fake = _fake_vision()
        fake.acquire_camera.side_effect = RuntimeError("no camera")

        with patch.object(puppet, "_vision", fake):
            assert puppet._check_camera() is False
            assert puppet._check_camera() is False

        fake.acquire_camera.assert_called_once()

    def test_a_busy_release_does_not_escape(self, fresh_camera_cache):
        from web.routes import puppet
        fake = _fake_vision()
        fake.release_camera.side_effect = _Busy("lock held")

        with patch.object(puppet, "_vision", fake):
            assert puppet._check_camera() is True

    def test_a_busy_release_is_not_a_500(self, fresh_camera_cache):
        fake = _fake_vision()
        fake.release_camera.side_effect = _Busy("lock held")
        client = get_client()

        with patch("web.routes.puppet._vision", fake):
            resp = client.get("/api/puppet/camera/status", headers=auth())

        assert resp.status_code == 200
        assert resp.json()["available"] is True

    def test_a_held_lock_does_not_block_past_the_timeout(self, fresh_camera_cache, monkeypatch):
        """The real camera.py and a real held lock, with a short timeout."""
        import time
        from web.routes import puppet

        camera_module = _real_camera(monkeypatch, MagicMock())
        camera_module._LOCK_TIMEOUT_S = 0.05
        assert camera_module._cam_lock.acquire(timeout=1)
        started = time.monotonic()
        try:
            with patch.object(puppet, "_vision", _vision_over(camera_module)):
                assert puppet._check_camera() is False
        finally:
            camera_module._cam_lock.release()

        assert time.monotonic() - started < 1.0
        assert puppet._camera_available_cache is None


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
