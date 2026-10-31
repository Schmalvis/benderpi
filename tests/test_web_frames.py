"""Web wiring for turn frames: the lifespan hook and GET /api/frames/status.

bender-web holds STOP BENDER, so a fault in the worker hook must never stop it
starting. These tests pin that, and pin that importing the app does no work.
"""
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import config
import turn_frames

PIN = "testpin"

# The plan's Status table, exactly.
STATUS_FIELDS = {
    "enabled_saved", "saved_today", "failed_today", "stale_today",
    "dropped_today", "last_saved", "waiting_notes", "oldest_note_age_s",
    "worker_alive", "worker_stuck_s",
}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Every test writes under tmp_path. No worker thread outlives a test."""
    logdir = tmp_path / "logs"
    logdir.mkdir()
    monkeypatch.setenv("BENDER_LOG_DIR", str(logdir))
    monkeypatch.setenv("BENDER_WEB_PIN", PIN)
    monkeypatch.setattr(config, "_DEFAULT_CONFIG_PATH",
                        str(tmp_path / "bender_config.json"))
    monkeypatch.setattr(turn_frames, "_current", None)
    monkeypatch.setattr(turn_frames, "_last_logged", {})
    yield
    turn_frames.stop_worker()


def get_app():
    import web.app
    importlib.reload(web.app)
    return web.app.app


def headers():
    from web.auth import issue_token
    return {"X-Bender-Token": issue_token()}


def worker_threads():
    return [t for t in threading.enumerate()
            if t.name == "turn-frames-worker" and t.is_alive()]


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------------------------------------------------------------------------
# GET /api/frames/status
# ---------------------------------------------------------------------------

class TestStatusRoute:
    def test_no_token_gives_401(self):
        from fastapi.testclient import TestClient
        client = TestClient(get_app())
        assert client.get("/api/frames/status").status_code == 401

    def test_a_bad_token_gives_401(self):
        from fastapi.testclient import TestClient
        client = TestClient(get_app())
        resp = client.get("/api/frames/status",
                          headers={"X-Bender-Token": "nonsense"})
        assert resp.status_code == 401

    def test_a_token_gives_every_status_field_and_no_more(self):
        from fastapi.testclient import TestClient
        client = TestClient(get_app())
        resp = client.get("/api/frames/status", headers=headers())
        assert resp.status_code == 200
        assert set(resp.json()) == STATUS_FIELDS

    def test_the_body_is_what_status_returns(self, tmp_path):
        from fastapi.testclient import TestClient
        (tmp_path / "bender_config.json").write_text(
            json.dumps({"turn_frames_enabled": True}))
        client = TestClient(get_app())
        body = client.get("/api/frames/status", headers=headers()).json()
        assert body["enabled_saved"] is True
        assert body["worker_alive"] is False

    def test_status_returns_at_once_while_the_camera_lock_is_held(self):
        from fastapi.testclient import TestClient
        client = TestClient(get_app())
        assert turn_frames.camera._cam_lock.acquire(timeout=1)
        try:
            started = time.monotonic()
            resp = client.get("/api/frames/status", headers=headers())
            elapsed = time.monotonic() - started
        finally:
            turn_frames.camera._cam_lock.release()
        assert resp.status_code == 200
        assert elapsed < 1.0

    def test_no_frames_route_serves_a_frame_or_takes_a_name(self):
        app = get_app()
        paths = {r.path for r in app.routes if "frames" in getattr(r, "path", "")}
        assert paths == {"/api/frames/status"}
        for r in app.routes:
            if getattr(r, "path", "") == "/api/frames/status":
                assert r.methods == {"GET"}


# ---------------------------------------------------------------------------
# The lifespan hook
# ---------------------------------------------------------------------------

class TestLifespan:
    def test_the_app_has_a_lifespan(self):
        app = get_app()
        assert app.router.lifespan_context is not None

    def test_with_testclient_starts_one_worker_and_exit_ends_it(self):
        from fastapi.testclient import TestClient
        app = get_app()
        assert worker_threads() == []
        with TestClient(app) as client:
            assert wait_for(lambda: len(worker_threads()) == 1)
            body = client.get("/api/frames/status", headers=headers()).json()
            assert body["worker_alive"] is True
        assert worker_threads() == []

    def test_two_start_worker_calls_make_one_thread(self):
        from fastapi.testclient import TestClient
        app = get_app()
        with TestClient(app):
            turn_frames.start_worker()
            turn_frames.start_worker()
            assert len(worker_threads()) == 1

    def test_the_worker_runs_its_startup_pass(self):
        from fastapi.testclient import TestClient
        app = get_app()
        with TestClient(app):
            assert wait_for(lambda: turn_frames._current.startup_done)

    def test_a_start_worker_that_raises_does_not_stop_the_app(self):
        from fastapi.testclient import TestClient
        app = get_app()
        with patch.object(turn_frames, "start_worker",
                          side_effect=RuntimeError("boom")):
            with TestClient(app) as client:
                resp = client.get("/api/frames/status", headers=headers())
                assert resp.status_code == 200

    def test_a_stop_worker_that_raises_does_not_stop_shutdown(self):
        from fastapi.testclient import TestClient
        app = get_app()
        with patch.object(turn_frames, "stop_worker",
                          side_effect=RuntimeError("boom")):
            with TestClient(app) as client:
                assert client.get("/api/frames/status", headers=headers()
                                  ).status_code == 200
            # Leaving the with block ran shutdown. It raised nothing.
        turn_frames.stop_worker()

    def test_shutdown_does_not_wait_on_the_camera(self):
        from fastapi.testclient import TestClient
        app = get_app()
        client = TestClient(app)
        client.__enter__()
        assert wait_for(lambda: len(worker_threads()) == 1)
        assert turn_frames.camera._cam_lock.acquire(timeout=1)
        try:
            started = time.monotonic()
            client.__exit__(None, None, None)
            elapsed = time.monotonic() - started
        finally:
            turn_frames.camera._cam_lock.release()
        assert elapsed < 3.0
        assert worker_threads() == []


# ---------------------------------------------------------------------------
# One camera singleton
# ---------------------------------------------------------------------------

class TestOneCamera:
    def test_turn_frames_and_vision_share_one_camera_module(self, tmp_path):
        """Checked in a fresh interpreter.

        Other test files put a fake `camera` into sys.modules, so an in-process
        check would test their leftovers and not the app's import graph.
        """
        code = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "import conftest  # hardware stubs only\n"
            "import web.app\n"
            "import turn_frames, vision, camera\n"
            "assert turn_frames.camera is vision.camera is camera\n"
            "assert sys.modules['camera'] is camera\n"
            "print('ok')\n"
            % (os.path.join(REPO, "scripts"), os.path.join(REPO, "tests"))
        )
        env = dict(os.environ, BENDER_LOG_DIR=str(tmp_path / "logs"),
                   BENDER_WEB_PIN=PIN)
        out = subprocess.run([sys.executable, "-c", code], env=env,
                             cwd=str(tmp_path), capture_output=True,
                             text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().endswith("ok")


# ---------------------------------------------------------------------------
# Importing the app does nothing
# ---------------------------------------------------------------------------

class TestImportDoesNoWork:
    def test_importing_the_app_starts_no_worker_and_sweeps_nothing(
            self, tmp_path):
        logdir = tmp_path / "subprocess-logs"
        expired = logdir / "frames" / "2000-01-01"
        expired.mkdir(parents=True)
        (expired / "outcomes.jsonl").write_text("{}\n")
        code = (
            "import sys, threading, time\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "import conftest  # hardware stubs only\n"
            "import web.app\n"
            "time.sleep(1.0)\n"
            "names = [t.name for t in threading.enumerate()]\n"
            "assert 'turn-frames-worker' not in names, names\n"
            "print('ok')\n"
            % (os.path.join(REPO, "scripts"), os.path.join(REPO, "tests"))
        )
        env = dict(os.environ, BENDER_LOG_DIR=str(logdir),
                   BENDER_WEB_PIN=PIN)
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(tmp_path),
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip().endswith("ok")
        assert expired.is_dir()
        assert (expired / "outcomes.jsonl").exists()
