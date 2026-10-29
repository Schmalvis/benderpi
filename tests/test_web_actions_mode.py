"""Tests for puppet-only mode and the end-session IPC notes it writes.

Puppet-only mode exists to make Bender DEAF, not to make him speak. Puppet
playback itself goes over the say-request IPC to the running process
(``say_request.py``), precisely so it does not pay the ~23s model reload. This
route is the other thing: the operator is about to speak as Bender and does not
want Bender answering the room over the top.

So the contract under test is narrow and specific:
  * entering ends a live session FIRST, then stops the service
  * entering clears the IPC notes AFTER the stop, when nothing can read them
  * leaving clears them BEFORE the start, so the first session is not killed
  * the order matters, and these tests assert the order, not just the calls
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

PIN = "testpin"


def get_client():
    os.environ["BENDER_WEB_PIN"] = PIN
    import importlib
    import web.app
    importlib.reload(web.app)
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def headers():
    from web.auth import issue_token
    return {"X-Bender-Token": issue_token()}


@pytest.fixture
def ipc(tmp_path, monkeypatch):
    """Point the three IPC paths at a temp dir and record what systemctl got."""
    from config import cfg
    import web.routes.actions as actions

    paths = {
        "session": tmp_path / ".session_active.json",
        "end": tmp_path / ".end_session",
        "abort": tmp_path / ".abort_playback",
    }
    monkeypatch.setattr(cfg, "session_file", str(paths["session"]))
    monkeypatch.setattr(cfg, "end_session_file", str(paths["end"]))
    monkeypatch.setattr(cfg, "abort_file", str(paths["abort"]))
    monkeypatch.setattr(actions, "_IS_LINUX", True)

    events = []

    def fake_guarded(cmd):
        # Snapshot which notes exist AT THE MOMENT systemctl runs. That is the
        # whole point: "we wrote them eventually" is not the contract.
        events.append({
            "cmd": list(cmd),
            "end_exists": paths["end"].exists(),
            "abort_exists": paths["abort"].exists(),
            "session_exists": paths["session"].exists(),
        })

    monkeypatch.setattr(actions, "_toggle_mode_guarded", fake_guarded)
    return paths, events


class TestEnteringPuppetOnly:

    def test_ends_a_live_session_before_stopping_the_service(self, ipc):
        paths, events = ipc
        paths["session"].write_text('{"active": true, "turns": 2}')
        client = get_client()

        resp = client.post("/api/actions/toggle-mode",
                           json={"mode": "puppet_only"}, headers=headers())

        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "puppet_only"
        assert body["session_ended"] is True, "a live session must be reported as ended"
        assert len(events) == 1
        assert events[0]["cmd"] == ["sudo", "systemctl", "stop", "bender-converse"]
        # Both notes were already on the floor when the stop ran, so a session
        # mid-reply stops speaking instead of being SIGTERMed mid-clip.
        assert events[0]["end_exists"] is True
        assert events[0]["abort_exists"] is True

    def test_clears_every_note_after_the_stop(self, ipc):
        paths, events = ipc
        paths["session"].write_text('{"active": true}')
        client = get_client()

        client.post("/api/actions/toggle-mode",
                    json={"mode": "puppet_only"}, headers=headers())

        # Nothing is left to consume them, and session-status reads the session
        # file directly -- a leftover would report a live conversation on a box
        # where nothing is listening.
        assert not paths["session"].exists()
        assert not paths["end"].exists()
        assert not paths["abort"].exists()

    def test_session_status_agrees_afterwards(self, ipc):
        paths, _ = ipc
        paths["session"].write_text('{"active": true, "turns": 3}')
        client = get_client()

        client.post("/api/actions/toggle-mode",
                    json={"mode": "puppet_only"}, headers=headers())
        resp = client.get("/api/actions/session-status", headers=headers())

        assert resp.json() == {"active": False}

    def test_works_with_no_session_live(self, ipc):
        paths, events = ipc
        client = get_client()

        resp = client.post("/api/actions/toggle-mode",
                           json={"mode": "puppet_only"}, headers=headers())

        assert resp.status_code == 200
        assert resp.json()["session_ended"] is False
        assert len(events) == 1, "the service is still stopped; there was just nothing to end"


class TestLeavingPuppetOnly:

    def test_clears_the_notes_before_starting(self, ipc):
        paths, events = ipc
        # Exactly the state puppet-only can leave behind if a stop raced a write.
        paths["end"].write_text("")
        paths["abort"].write_text("")
        client = get_client()

        resp = client.post("/api/actions/toggle-mode",
                           json={"mode": "converse"}, headers=headers())

        assert resp.status_code == 200
        assert len(events) == 1
        assert events[0]["cmd"] == ["sudo", "systemctl", "start", "bender-converse"]
        # The floor must be clear BEFORE the process comes up. A leftover
        # end-session note would end the first real conversation on turn one.
        assert events[0]["end_exists"] is False
        assert events[0]["abort_exists"] is False

    def test_does_not_claim_to_have_ended_a_session(self, ipc):
        paths, _ = ipc
        paths["session"].write_text('{"active": true}')
        client = get_client()

        resp = client.post("/api/actions/toggle-mode",
                           json={"mode": "converse"}, headers=headers())

        # Leaving puppet mode starts the service. It does not end anything.
        assert resp.json()["session_ended"] is False


class TestModeValidation:

    @pytest.mark.parametrize("mode", ["", "PUPPET_ONLY", "off", "stop", "puppet"])
    def test_rejects_anything_else(self, ipc, mode):
        _, events = ipc
        client = get_client()
        resp = client.post("/api/actions/toggle-mode",
                           json={"mode": mode}, headers=headers())
        assert resp.status_code == 400
        assert events == [], "a bad mode must never touch systemctl"

    def test_a_busy_bender_returns_409_not_500(self, ipc, monkeypatch):
        import web.routes.actions as actions
        from web.service_guard import ServiceBusy

        def busy(cmd):
            raise ServiceBusy("held")

        monkeypatch.setattr(actions, "_toggle_mode_guarded", busy)
        client = get_client()
        resp = client.post("/api/actions/toggle-mode",
                           json={"mode": "puppet_only"}, headers=headers())
        assert resp.status_code == 409


class TestTheNotesThemselves:

    def test_end_live_session_reports_only_a_real_session(self, ipc):
        paths, _ = ipc
        import web.routes.actions as actions

        assert actions._end_live_session() is False
        paths["session"].write_text("{}")
        assert actions._end_live_session() is True

    def test_end_live_session_writes_both_notes(self, ipc):
        paths, _ = ipc
        import web.routes.actions as actions

        actions._end_live_session()
        assert paths["end"].exists()
        assert paths["abort"].exists()

    def test_clear_session_ipc_is_idempotent(self, ipc):
        paths, _ = ipc
        import web.routes.actions as actions

        actions._clear_session_ipc()          # nothing there yet
        for p in paths.values():
            p.write_text("")
        actions._clear_session_ipc()
        actions._clear_session_ipc()          # again, on an empty dir
        for p in paths.values():
            assert not p.exists()


class TestARefusedStartIsReported:
    """Found 2026-10-29. Both routes ignored systemctl's exit code, so after
    StartLimitBurst (5 starts per 300s) the phone showed a green "Bender
    starting" toast, or "Halloween mode is live", while Bender stayed down
    and only SSH could recover him. service_guard._start_converse() already
    checked the code and retried after reset-failed; these routes did not
    use it.

    Does NOT use the `ipc` fixture: that one replaces the very function under
    test. subprocess.run is faked in both modules, so nothing reaches the real
    systemctl.
    """

    @pytest.fixture
    def systemctl(self, tmp_path, monkeypatch):
        """`refuse` lists the verbs systemctl fails, the way start-limit-hit
        does; `fail_first` fails only the first call of a verb."""
        import types
        from config import cfg
        import web.routes.actions as actions
        import web.service_guard as guard

        for attr, name in (("session_file", ".session_active.json"),
                           ("end_session_file", ".end_session"),
                           ("abort_file", ".abort_playback")):
            monkeypatch.setattr(cfg, attr, str(tmp_path / name))

        calls, refuse, fail_first = [], set(), set()

        def run(cmd, **kw):
            calls.append(list(cmd))
            verb = next((v for v in ("reset-failed", "restart", "start", "stop")
                         if v in cmd), None)
            failed = verb in refuse or verb in fail_first
            fail_first.discard(verb)
            return types.SimpleNamespace(
                returncode=1 if failed else 0, stdout="",
                stderr="Job failed: start-limit-hit" if failed else "")

        monkeypatch.setattr(actions.subprocess, "run", run)
        monkeypatch.setattr(guard.subprocess, "run", run)
        monkeypatch.setattr(actions, "_IS_LINUX", True)
        monkeypatch.setattr(guard, "_IS_LINUX", True)
        return types.SimpleNamespace(calls=calls, refuse=refuse,
                                     fail_first=fail_first)

    RESET = ["sudo", "-n", "systemctl", "reset-failed", "bender-converse"]

    def _post(self, path, body=None):
        return get_client().post(path, json=body or {}, headers=headers())

    def test_start_bender_reports_a_refused_start(self, systemctl):
        systemctl.refuse.add("start")
        resp = self._post("/api/actions/toggle-mode", {"mode": "converse"})
        assert resp.status_code == 503, resp.text
        assert "did not start" in resp.json()["detail"]
        assert self.RESET in systemctl.calls, \
            "a refused start must try reset-failed before giving up"

    def test_start_bender_recovers_through_reset_failed(self, systemctl):
        systemctl.fail_first.add("start")
        resp = self._post("/api/actions/toggle-mode", {"mode": "converse"})
        assert resp.status_code == 200, resp.text
        assert self.RESET in systemctl.calls

    def test_stop_bender_reports_a_failed_stop(self, systemctl):
        """STOP is the only thing that silences Halloween mode. A stop that
        failed must not turn the button green."""
        systemctl.refuse.add("stop")
        resp = self._post("/api/actions/toggle-mode", {"mode": "puppet_only"})
        assert resp.status_code == 503, resp.text

    def test_save_and_restart_reports_a_refused_restart(self, systemctl):
        systemctl.refuse.update({"restart", "start"})
        resp = self._post("/api/actions/restart")
        assert resp.status_code == 503, resp.text
        assert "did not start" in resp.json()["detail"]

    def test_save_and_restart_recovers_from_the_start_limit(self, systemctl):
        # At the start limit the first plain start is refused too; only
        # reset-failed clears it.
        systemctl.refuse.add("restart")
        systemctl.fail_first.add("start")
        resp = self._post("/api/actions/restart")
        assert resp.status_code == 200, resp.text
        assert self.RESET in systemctl.calls
