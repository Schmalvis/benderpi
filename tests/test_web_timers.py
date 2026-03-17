"""Tests for timer web API."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

PIN = "testpin"


def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    import importlib
    import web.app
    importlib.reload(web.app)
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def auth():
    return {"X-Bender-Pin": PIN}


def test_list_timers_empty(monkeypatch, tmp_path):
    import timers
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    resp = client.get("/api/timers", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["timers"] == []


def test_create_timer(monkeypatch, tmp_path):
    import timers
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    resp = client.post("/api/timers", json={"label": "test", "duration_s": 300}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["timer"]["label"] == "test"
    # Verify it's in the list
    resp = client.get("/api/timers", headers=auth())
    assert len(resp.json()["timers"]) == 1


def test_create_alarm(monkeypatch, tmp_path):
    import timers
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    fires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    resp = client.post("/api/timers", json={"label": "alarm_test", "fires_at": fires_at}, headers=auth())
    assert resp.status_code == 200
    assert resp.json()["timer"]["label"] == "alarm_test"
    assert resp.json()["timer"]["type"] == "alarm"


def test_create_timer_missing_params(monkeypatch, tmp_path):
    import timers
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    resp = client.post("/api/timers", json={"label": "bad"}, headers=auth())
    assert resp.status_code == 400


def test_cancel_timer(monkeypatch, tmp_path):
    import timers
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    resp = client.post("/api/timers", json={"label": "cancel_me", "duration_s": 60}, headers=auth())
    timer_id = resp.json()["timer"]["id"]
    resp = client.delete("/api/timers/" + timer_id, headers=auth())
    assert resp.status_code == 200
    resp = client.get("/api/timers", headers=auth())
    assert len(resp.json()["timers"]) == 0


def test_cancel_nonexistent(monkeypatch, tmp_path):
    import timers
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    resp = client.delete("/api/timers/t_nonexistent", headers=auth())
    assert resp.status_code == 404


def test_dismiss_timer(monkeypatch, tmp_path):
    import timers
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    # Create a timer that fires immediately (duration 0)
    resp = client.post("/api/timers", json={"label": "dismiss_me", "duration_s": 0}, headers=auth())
    timer_id = resp.json()["timer"]["id"]
    resp = client.post("/api/timers/" + timer_id + "/dismiss", headers=auth())
    assert resp.status_code == 200
    # Should no longer appear in list (dismissed)
    resp = client.get("/api/timers", headers=auth())
    assert len(resp.json()["timers"]) == 0


def test_dismiss_nonexistent(monkeypatch, tmp_path):
    import timers
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    resp = client.post("/api/timers/t_nonexistent/dismiss", headers=auth())
    assert resp.status_code == 404


def test_dismiss_all(monkeypatch, tmp_path):
    import timers
    monkeypatch.setattr(timers, "_FILE", str(tmp_path / "timers.json"))
    monkeypatch.setattr(timers, "_TMP_FILE", str(tmp_path / "timers.json.tmp"))
    monkeypatch.setattr(timers, "_cache", None)
    client = get_client()
    # Create two timers that fire immediately
    client.post("/api/timers", json={"label": "a", "duration_s": 0}, headers=auth())
    client.post("/api/timers", json={"label": "b", "duration_s": 0}, headers=auth())
    resp = client.post("/api/timers/dismiss-all", headers=auth())
    assert resp.status_code == 200
    assert resp.json()["dismissed"] == 2
    resp = client.get("/api/timers", headers=auth())
    assert len(resp.json()["timers"]) == 0


def test_timers_require_pin():
    client = get_client()
    resp = client.get("/api/timers")
    assert resp.status_code == 401
