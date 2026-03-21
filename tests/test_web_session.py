"""Tests for session status and end-session IPC."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"
_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)

def auth():
    return {"X-Bender-Pin": PIN}

def test_session_status_inactive():
    path = os.path.join(_BASE_DIR, ".session_active.json")
    if os.path.exists(path):
        os.unlink(path)
    client = get_client()
    resp = client.get("/api/actions/session-status", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["active"] is False

def test_session_status_active():
    path = os.path.join(_BASE_DIR, ".session_active.json")
    try:
        with open(path, "w") as f:
            json.dump({"active": True, "session_id": "test123", "turns": 2}, f)
        client = get_client()
        resp = client.get("/api/actions/session-status", headers=auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["session_id"] == "test123"
    finally:
        if os.path.exists(path):
            os.unlink(path)

def test_end_session_no_active():
    for p in [os.path.join(_BASE_DIR, ".session_active.json"), os.path.join(_BASE_DIR, ".end_session")]:
        if os.path.exists(p):
            os.unlink(p)
    client = get_client()
    resp = client.post("/api/actions/end-session", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_session"

def test_end_session_creates_flag():
    session_path = os.path.join(_BASE_DIR, ".session_active.json")
    flag_path = os.path.join(_BASE_DIR, ".end_session")
    try:
        with open(session_path, "w") as f:
            json.dump({"active": True, "session_id": "test456", "turns": 1}, f)
        client = get_client()
        resp = client.post("/api/actions/end-session", headers=auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert os.path.exists(flag_path)
    finally:
        for p in [session_path, flag_path]:
            if os.path.exists(p):
                os.unlink(p)


def test_end_session_creates_abort_file():
    session_path = os.path.join(_BASE_DIR, ".session_active.json")
    end_session_path = os.path.join(_BASE_DIR, ".end_session")
    abort_path = os.path.join(_BASE_DIR, ".abort_playback")
    try:
        with open(session_path, "w") as f:
            json.dump({"active": True, "session_id": "test789", "turns": 1}, f)
        client = get_client()
        resp = client.post("/api/actions/end-session", headers=auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert os.path.exists(end_session_path)
        assert os.path.exists(abort_path)
    finally:
        for p in [session_path, end_session_path, abort_path]:
            if os.path.exists(p):
                os.unlink(p)
