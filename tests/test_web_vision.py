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
    from web.auth import issue_token
    return {"X-Bender-Token": issue_token()}


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




# ---------------------------------------------------------------------------
# POST /api/vision/analyse must refuse rather than invent a room
# ---------------------------------------------------------------------------
#
# Speaking is expensive and it cannot be taken back: _speak_in_background takes
# service_lease(), which stops and restarts bender-converse, so one click costs
# about 23s of deafness. Until 2026-10-15 the reachable outcome of a click on
# this device was paying that to hear Bender react to a room nothing had looked
# at, because:
#
#   * cfg.vlm_enabled was read ONLY by wake_converse.py, so this route tried
#     the VLM however the flag was set, and
#   * vlm.describe_scene returned "" for a failed look AND for an empty answer,
#     so scene.is_empty() was True either way, and
#   * the route then prompted the AI with "detected nobody".
#
# The VLM cannot load on this device at all while bender-converse runs: the
# on-chip KV-Cache is a singleton and the resident conversation LLM holds it
# (measured 2026-07-30). So the invented answer was the only one available.

import pytest


# Bound at import, before any test stubs sys.modules["vision"]. A lambda that
# ran `from vision import SceneDescription` inside patch.dict hit the stub and
# raised ImportError.
from vision import SceneDescription as _SceneDescription


def _scene(description="", error=""):
    return _SceneDescription(description=description, error=error)


@pytest.fixture
def vision_on(monkeypatch):
    """cfg.vlm_enabled true, restored afterwards (cfg is a singleton)."""
    from config import cfg
    monkeypatch.setattr(cfg, "vlm_enabled", True)
    return cfg


# `web.routes.vision` binds `_vision`, `audio` and `leds` at MODULE level, and
# `tests/test_vision.py` calls `importlib.reload(vision)`. So after that file
# runs, sys.modules["vision"] is a NEW object while the route still holds the
# old one, and patch.dict(sys.modules, ...) no longer reaches the route at all.
# These three tests passed alone and failed in the full suite, where the real
# Picamera2 was then constructed and raised "No camera number 0 found".
# Patch the route module's own names instead. Same reasoning as the
# `_ANALYSE_SCENE` note in tests/test_vision_handler.py.
def _route_patches(scene, spoken):
    """Context managers that stub the route's module-level dependencies."""
    import web.routes.vision as vroute
    tts = types.ModuleType("tts_generate")
    tts.speak_streaming = lambda t: (spoken.append(t), iter(()))[1]

    class _MockAIResponder:
        def respond(self, prompt):
            return "Mock Bender response."

    ai = types.ModuleType("ai_response")
    ai.AIResponder = _MockAIResponder
    return vroute, [
        patch.object(vroute._vision, "analyse_scene", return_value=scene),
        patch.object(vroute, "audio", MagicMock()),
        patch.object(vroute, "leds", MagicMock()),
        patch.object(vroute, "service_lease", MagicMock()),
        patch.dict(sys.modules, {"tts_generate": tts, "ai_response": ai}),
    ]


def _call(scene, spoken, recorder=None):
    """POST the route with analyse_scene stubbed; record anything spoken."""
    client = get_client()
    vroute, patches = _route_patches(scene, spoken)
    if recorder is not None:
        sys.modules["ai_response"].AIResponder = recorder
    from contextlib import ExitStack
    with ExitStack() as stack:
        for pt in patches:
            stack.enter_context(pt)
        if recorder is not None:
            sys.modules["ai_response"].AIResponder = recorder
        return client.post("/api/vision/analyse", headers=auth())


def test_refuses_with_503_when_vision_is_switched_off(monkeypatch):
    """The flag the route used to ignore."""
    from config import cfg
    monkeypatch.setattr(cfg, "vlm_enabled", False)
    import web.routes.vision as vroute
    client = get_client()
    with patch.object(vroute._vision, "analyse_scene",
                      return_value=_scene("a person")) as mock_look:
        resp = client.post("/api/vision/analyse", headers=auth())
    assert resp.status_code == 503
    assert "vlm_enabled" in resp.json()["detail"]
    # It must refuse BEFORE touching the camera or the Hailo chip.
    mock_look.assert_not_called()


def test_a_failed_look_is_502_and_nothing_is_spoken(vision_on):
    spoken = []
    resp = _call(_scene(error="the vision model did not answer"), spoken)
    assert resp.status_code == 502
    assert "did not look" in resp.json()["detail"]
    assert "the vision model did not answer" in resp.json()["detail"]
    assert spoken == []


def test_an_empty_description_is_502_not_detected_nobody(vision_on):
    """An empty answer is not evidence of an empty room."""
    spoken = []
    resp = _call(_scene(description=""), spoken)
    assert resp.status_code == 502
    assert "not" in resp.json()["detail"]
    assert "nobody" not in resp.text.lower(), resp.text
    assert spoken == []


def test_a_real_description_is_spoken(vision_on):
    spoken = []
    resp = _call(_scene(description="A person is reading a book on the sofa."), spoken)
    assert resp.status_code == 200
    data = resp.json()
    assert data["text"] == "Mock Bender response."
    assert data["description"] == "A person is reading a book on the sofa."


def test_the_prompt_carries_the_real_scene_and_never_fabricates(vision_on):
    """The AI must be told what was seen, verbatim."""
    seen = []

    class _Recorder:
        def respond(self, prompt):
            seen.append(prompt)
            return "Mock Bender response."

    resp = _call(_scene(description="two cats on a rug"), [], recorder=_Recorder)
    assert resp.status_code == 200
    assert len(seen) == 1
    assert "two cats on a rug" in seen[0]
    assert "nobody" not in seen[0].lower()


# ---------------------------------------------------------------------------
# The deleted passive-mode routes must stay deleted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_api_vision_passive_does_not_exist(method):
    """bfbd831 deleted these on 2026-04-02, with the watcher thread behind them.

    The Config page kept calling all three for six months and polled the GET
    every 60 seconds. The device journal held 93 404s by 2026-10-15. The
    frontend calls are gone now; this is the guard against re-adding one.
    """
    client = get_client()
    from web.app import app
    paths = app.openapi()["paths"]
    resp = getattr(client, method)("/api/vision/passive", headers=auth())
    # The route is simply absent. Use the spec, not app.routes: app.routes does
    # not enumerate reliably on FastAPI 0.139 / Starlette 1.3.
    assert "/api/vision/passive" not in paths
    # GET falls through to the StaticFiles mount and 404s. POST and DELETE get
    # 405 from that mount, which serves GET/HEAD only. Neither is a success.
    assert resp.status_code in (404, 405), resp.status_code
