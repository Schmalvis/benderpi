"""Task 7 of the turn frames plan: the two hooks into bender-converse.

`ConversationSession._snap()` asks for a frame as Bender starts to reply.
`_halloween_loop` calls `turn_frames.warm()` once the presence gate has passed.

This is the hot path. The feature must never stop him talking, so most of
these tests are about a hook that RAISES and a turn that still completes.

The session tests use a real `ConversationSession`, a real `SessionLogger` and
the real `turn_frames`. Only `audio`, `leds`, TTS, the responder and the AI are
stubbed. They read the written log lines and the written notes back from disk.
"""
import ast
import datetime
import json
import os
import sys
import types

import pytest

import conversation_log
import presence
import session as session_mod
import turn_frames
from config import cfg
from handler_base import Response
from session import ConversationSession

from tests.test_halloween_loop import wc  # noqa: F401  (fixture)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")


# ---------------------------------------------------------------------------
# Rig
# ---------------------------------------------------------------------------

class _Responder:
    """Answers every turn with a streaming-AI style reply, or raises."""

    def __init__(self):
        self.raise_next = None

    def will_need_thinking(self, text):
        return False

    def get_response(self, text, ai, ai_local=None):
        if self.raise_next is not None:
            exc, self.raise_next = self.raise_next, None
            raise exc
        return Response(text="Fine.", method="ai_fallback", intent="UNKNOWN",
                        model="fake", wav_path=None)


class Rig:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.logs = tmp_path / "logs"
        self.responder = _Responder()
        self.aborted = False
        self.session = None

    # -- disk ---------------------------------------------------------------

    def rows(self):
        """Every JSON line written by conversation_log, in order."""
        out = []
        for p in sorted(self.logs.glob("*.jsonl")):
            out.extend(json.loads(line) for line in p.read_text().splitlines())
        return out

    def turns(self):
        return [r for r in self.rows() if r["type"] == "turn"]

    def notes(self):
        q = os.path.join(turn_frames.frames_dir(), ".queue")
        return sorted(os.listdir(q)) if os.path.isdir(q) else []

    def frames_dir_exists(self):
        return os.path.exists(turn_frames.frames_dir())

    def consume_notes(self):
        """Stand in for the worker taking the notes."""
        q = os.path.join(turn_frames.frames_dir(), ".queue")
        for n in os.listdir(q):
            os.unlink(os.path.join(q, n))

    # -- session ------------------------------------------------------------

    def make(self):
        self.session = ConversationSession(
            ai=None, ai_local=None, responder=self.responder,
            session_log=conversation_log.SessionLogger(), vision=None)
        return self.session

    @property
    def log_(self):
        return self.session._session_log


@pytest.fixture
def rig(monkeypatch, tmp_path):
    r = Rig(tmp_path)
    r.logs.mkdir()
    monkeypatch.setenv("BENDER_LOG_DIR", str(r.logs))
    monkeypatch.setattr(conversation_log, "LOG_DIR", str(r.logs))
    # The IPC notes live in the repo root by default. Keep them out of it.
    for key in ("end_session_file", "abort_file", "session_file"):
        monkeypatch.setattr(cfg, key, str(tmp_path / key), raising=False)
    monkeypatch.setattr(cfg, "turn_frames_enabled", True, raising=False)
    monkeypatch.setattr(cfg, "thinking_sound", False, raising=False)
    monkeypatch.setattr(cfg, "silent_wakeword", False, raising=False)
    monkeypatch.setattr(cfg, "dismissal_ends_session", True, raising=False)
    monkeypatch.setattr(cfg, "response_hard_timeout_s", 5, raising=False)

    stub_audio = types.SimpleNamespace(
        open_session=lambda: None, close_session=lambda: None,
        play=lambda *a, **k: None, play_stream=lambda *a, **k: None,
        abort=lambda: None, was_aborted=lambda: r.aborted)
    monkeypatch.setattr(session_mod, "audio", stub_audio)
    monkeypatch.setattr(session_mod, "leds", types.SimpleNamespace(
        set_talking=lambda *a: None, all_off=lambda *a: None))
    monkeypatch.setattr(session_mod, "tts_generate", types.SimpleNamespace(
        speak_streaming=lambda t: iter(()),
        speak_from_iter=lambda it: iter(())))
    monkeypatch.setattr(session_mod, "halloween_enabled", lambda: False)
    monkeypatch.setattr(presence, "last_reading", lambda: (812, 0.4))
    r.make()
    return r


def _day():
    return datetime.datetime.now().strftime("%Y-%m-%d")


FRAME_KEYS = ("frame_requested", "presence_mm", "presence_age_s")


# ---------------------------------------------------------------------------
# A failing hook never stops a turn
# ---------------------------------------------------------------------------

class TestAFailingHookNeverStopsATurn:
    def test_a_raising_request_does_not_stop_the_turn(self, rig, monkeypatch):
        def boom(name):
            raise RuntimeError("disk on fire")
        monkeypatch.setattr(turn_frames, "request", boom)
        result = rig.session.handle_turn("hello there")
        assert result.should_end is False
        assert result.intent == "UNKNOWN"
        rows = rig.turns()
        assert len(rows) == 1                       # the turn is still logged
        assert rows[0]["user_text"] == "hello there"
        assert not any(k in rows[0] for k in FRAME_KEYS)

    def test_a_raising_last_reading_does_not_stop_the_turn(self, rig,
                                                           monkeypatch):
        def boom():
            raise RuntimeError("sensor on fire")
        monkeypatch.setattr(presence, "last_reading", boom)
        result = rig.session.handle_turn("hello there")
        assert result.should_end is False
        rows = rig.turns()
        assert len(rows) == 1
        assert rows[0]["response_text"] == "Fine."
        # request() ran and left a note, but the note() call never happened
        assert not any(k in rows[0] for k in FRAME_KEYS)

    def test_a_raising_note_does_not_stop_the_turn(self, rig, monkeypatch):
        def boom(**kw):
            raise RuntimeError("note on fire")
        monkeypatch.setattr(rig.log_, "note", boom)
        assert rig.session.handle_turn("hello").should_end is False
        assert len(rig.turns()) == 1

    def test_a_failure_is_logged_as_a_warning(self, rig, monkeypatch, caplog):
        def boom(name):
            raise RuntimeError("disk on fire")
        monkeypatch.setattr(turn_frames, "request", boom)
        with caplog.at_level("WARNING"):
            rig.session.handle_turn("hello")
        assert any("turn frame request failed" in r.getMessage()
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# What the turn entry gains
# ---------------------------------------------------------------------------

class TestTheTurnEntry:
    def test_enabled_turn_gains_the_three_fields(self, rig):
        rig.session.handle_turn("hello")
        row = rig.turns()[0]
        assert row["frame_requested"] == f"{_day()}/{row['session_id']}_t01.jpg"
        assert row["presence_mm"] == 812
        assert row["presence_age_s"] == 0.4

    def test_never_read_sensor_gives_null_null_but_keeps_the_keys(self, rig,
                                                                  monkeypatch):
        monkeypatch.setattr(presence, "last_reading", lambda: (None, None))
        rig.session.handle_turn("hello")
        row = rig.turns()[0]
        assert row["presence_mm"] is None
        assert row["presence_age_s"] is None
        assert "frame_requested" in row

    def test_disabled_turn_has_none_of_the_three_and_no_note(self, rig,
                                                             monkeypatch):
        monkeypatch.setattr(cfg, "turn_frames_enabled", False, raising=False)
        rig.session.handle_turn("hello")
        row = rig.turns()[0]
        assert not any(k in row for k in FRAME_KEYS)
        assert rig.notes() == []
        assert rig.log_._held == {}

    def test_request_returning_none_leaves_the_entry_unchanged(self, rig,
                                                               monkeypatch):
        monkeypatch.setattr(turn_frames, "request", lambda name: None)
        rig.session.handle_turn("hello")
        row = rig.turns()[0]
        assert not any(k in row for k in FRAME_KEYS)

    def test_a_note_never_overwrites_a_core_field(self, rig):
        rig.session.handle_turn("hello")
        row = rig.turns()[0]
        assert row["type"] == "turn"
        assert row["turn"] == 1
        assert row["user_text"] == "hello"


class TestOffDoesNoFilesystemWork:
    def test_nothing_is_created_under_frames_dir(self, rig, monkeypatch):
        monkeypatch.setattr(cfg, "turn_frames_enabled", False, raising=False)
        seen = []
        monkeypatch.setattr(turn_frames, "_ensure_queue",
                            lambda: seen.append(1))
        rig.session.start()
        rig.session.handle_turn("one")
        rig.session.handle_turn("two")
        assert seen == []
        assert not rig.frames_dir_exists()

    def test_null_and_truthy_non_bool_also_count_as_off(self, rig, monkeypatch):
        for value in (None, 1, "true"):
            monkeypatch.setattr(cfg, "turn_frames_enabled", value, raising=False)
            rig.session.handle_turn("hello")
        assert not rig.frames_dir_exists()


# ---------------------------------------------------------------------------
# Names join a frame to its log row
# ---------------------------------------------------------------------------

class TestNamesMatchLogRows:
    def test_three_turns_after_start_each_note_matches_its_row(self, rig):
        # start() first: the greeting calls log_turn and moves the count.
        rig.session.start()
        for text in ("one", "two", "three"):
            rig.session.handle_turn(text)
        rows = rig.turns()
        assert [r["turn"] for r in rows] == [1, 2, 3, 4]
        greeting, replies = rows[0], rows[1:]
        assert "frame_requested" not in greeting
        sid = rig.log_.session_id
        expected = {f"{_day()}__{sid}_t{r['turn']:02d}" for r in replies}
        assert set(rig.notes()) == expected
        assert len(rig.notes()) == 3
        for r in replies:
            assert r["session_id"] == sid
            assert r["frame_requested"] == f"{_day()}/{sid}_t{r['turn']:02d}.jpg"

    def test_the_name_is_the_turn_that_will_be_logged(self, rig, monkeypatch):
        seen = []
        real = turn_frames.request

        def spy(name):
            seen.append(name)
            return real(name)

        monkeypatch.setattr(turn_frames, "request", spy)
        rig.session.handle_turn("one")
        row = rig.turns()[0]
        assert seen == [f"{row['session_id']}_t{row['turn']:02d}"]


# ---------------------------------------------------------------------------
# Paths that make no frame
# ---------------------------------------------------------------------------

class TestNoFrameRequested:
    def test_an_end_session_note_makes_no_request(self, rig, monkeypatch):
        calls = []
        monkeypatch.setattr(turn_frames, "request",
                            lambda name: calls.append(name))
        open(cfg.end_session_file, "w").close()
        result = rig.session.handle_turn("hello")
        assert result.end_reason == "remote_abrupt"
        assert calls == []
        assert rig.notes() == []
        assert rig.turns() == []

    @pytest.mark.parametrize("halloween", [True, False])
    def test_start_makes_no_request(self, rig, monkeypatch, halloween):
        calls = []
        monkeypatch.setattr(turn_frames, "request",
                            lambda name: calls.append(name))
        monkeypatch.setattr(turn_frames, "warm", lambda: calls.append("warm"))
        monkeypatch.setattr(session_mod, "halloween_enabled",
                            lambda: halloween)
        rig.session.start()
        assert calls == []
        assert not rig.frames_dir_exists()
        # the greeting is logged, and carries no frame fields
        rows = rig.turns()
        assert len(rows) == 1
        assert not any(k in rows[0] for k in FRAME_KEYS)


class TestAHeldNoteDoesNotLeak:
    def test_a_note_held_at_session_end_never_reaches_the_next_session(
            self, rig, monkeypatch):
        # This is the plan's claim. Every path with no log_turn ends the
        # session, and the next session has its own logger.
        rig.responder.raise_next = RuntimeError("model fell over")
        with pytest.raises(RuntimeError):
            rig.session.handle_turn("first")
        assert rig.log_._held                      # precondition: it is held
        rig.session.end("error")
        assert not any(k in row for row in rig.rows() for k in FRAME_KEYS)
        # The next turn makes no note of its own, so a leak has nowhere to
        # hide. A turn that makes one would overwrite it.
        rig.make()
        monkeypatch.setattr(turn_frames, "request", lambda name: None)
        rig.session.handle_turn("second")
        row = rig.turns()[-1]
        assert row["user_text"] == "second"
        assert not any(k in row for k in FRAME_KEYS)

    def test_a_later_turn_writes_its_own_values(self, rig, monkeypatch):
        # Turn 1 raises after _snap and before log_turn: the note is still held.
        rig.responder.raise_next = RuntimeError("model fell over")
        with pytest.raises(RuntimeError):
            rig.session.handle_turn("first")
        assert rig.turns() == []
        assert rig.log_._held                      # precondition: it is held
        rig.consume_notes()                        # the worker took the note

        # Turn 2 runs on the same logger with different readings.
        monkeypatch.setattr(presence, "last_reading", lambda: (333, 1.5))
        rig.session.handle_turn("second")
        rows = rig.turns()
        assert len(rows) == 1
        row = rows[0]
        assert row["turn"] == 1
        assert row["user_text"] == "second"
        assert row["presence_mm"] == 333           # its own value, not 812
        assert row["presence_age_s"] == 1.5
        assert row["frame_requested"] == f"{_day()}/{row['session_id']}_t01.jpg"
        assert set(k for k in row if k in FRAME_KEYS) == set(FRAME_KEYS)
        assert rig.log_._held == {}                # cleared by log_turn

    def test_the_turn_after_that_carries_nothing_from_the_first(self, rig,
                                                                monkeypatch):
        rig.responder.raise_next = RuntimeError("model fell over")
        with pytest.raises(RuntimeError):
            rig.session.handle_turn("first")
        rig.session.handle_turn("second")           # logs, clears the note
        monkeypatch.setattr(cfg, "turn_frames_enabled", False, raising=False)
        rig.session.handle_turn("third")
        third = rig.turns()[-1]
        assert third["user_text"] == "third"
        assert not any(k in third for k in FRAME_KEYS)

    def test_an_aborted_turn_drops_its_note_with_the_session(self, rig):
        rig.aborted = True
        result = rig.session.handle_turn("hello")
        assert result.end_reason == "aborted"
        assert rig.turns() == []                    # no log_turn on this path
        assert any(r["type"] == "session_end" for r in rig.rows())


# ---------------------------------------------------------------------------
# Hook positions, by reading the source
# ---------------------------------------------------------------------------

def _tree(name):
    with open(os.path.join(SCRIPTS, name)) as f:
        return ast.parse(f.read())


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _calls(node, attr):
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


class TestHookPositions:
    def test_snap_sits_after_the_end_session_check_and_before_inference(self):
        fn = _func(_tree("session.py"), "handle_turn")
        body = fn.body

        def idx(pred):
            for i, s in enumerate(body):
                if pred(s):
                    return i
            raise AssertionError("statement not found")

        end_check = idx(lambda s: isinstance(s, ast.If)
                        and "end_session_file" in ast.dump(s.test))
        snap = idx(lambda s: isinstance(s, ast.Expr)
                   and _calls(s, "_snap"))
        holder = idx(lambda s: isinstance(s, ast.AnnAssign)
                     and getattr(s.target, "id", "") == "_resp_holder")
        assert end_check < snap < holder
        assert snap == end_check + 1, "nothing may sit between the two"
        # the call is a direct statement, so it runs on the handle_turn thread
        assert len(_calls(fn, "_snap")) == 1

    def test_snap_is_not_called_from_the_inference_thread(self):
        fn = _func(_tree("session.py"), "handle_turn")
        infer = _func(fn, "_infer")
        assert _calls(infer, "_snap") == []

    def test_snap_is_not_called_anywhere_else(self):
        tree = _tree("session.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name not in (
                    "handle_turn", "_snap"):
                assert _calls(node, "_snap") == [], node.name

    def test_session_imports_both_modules_at_the_top(self):
        tree = _tree("session.py")
        top = {a.name for n in tree.body if isinstance(n, ast.Import)
               for a in n.names}
        assert {"presence", "turn_frames"} <= top

    def test_warm_is_called_only_in_the_halloween_loop(self):
        tree = _tree("wake_converse.py")
        sites = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if any(isinstance(c.func.value, ast.Name)
                       and c.func.value.id == "turn_frames"
                       for c in _calls(node, "warm")):
                    sites.append(node.name)
        assert sites == ["_halloween_loop"]
        uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name)
                and n.id == "turn_frames"]
        assert len(uses) == 1, "no other turn_frames hook in wake_converse.py"

    def test_warm_sits_between_the_gate_and_the_capture(self):
        src = open(os.path.join(SCRIPTS, "wake_converse.py")).read()
        start = src.index("def _halloween_loop")
        body = src[start:]
        gate = body.index("presence.wait_for_presence(")
        warm = body.index("turn_frames.warm()")
        listening = body.index("leds.set_listening(True)")
        capture = body.index("stt.listen_and_transcribe(after_playback=False)")
        assert gate < warm < listening < capture


# ---------------------------------------------------------------------------
# _halloween_loop
# ---------------------------------------------------------------------------

class TestHalloweenLoopWarm:
    def _listen_recorder(self, wc, events, heard):
        heard = list(heard)

        def listen(after_playback=True):
            events.append("listen")
            if not heard:
                raise KeyboardInterrupt
            return heard.pop(0)

        wc.stt = types.SimpleNamespace(listen_and_transcribe=listen,
                                       release=lambda: None)

    def _run(self, wc):
        with pytest.raises((KeyboardInterrupt, IndexError, SystemExit)):
            wc._halloween_loop(None, object(), object(), wc.log)

    def _usable_sensor(self, wc, monkeypatch):
        """A sensor that works, with the gate open. warm() needs both."""
        monkeypatch.setattr(wc.presence, "available", lambda: True)
        monkeypatch.setattr(wc.presence, "wait_for_presence", lambda s: True)

    def test_a_raising_warm_does_not_stop_the_loop_or_the_capture(
            self, wc, monkeypatch, caplog):
        events = []

        def boom():
            events.append("warm")
            # Bounded. Without the guard this raise reaches the loop's outer
            # except, the capture never runs, and `listen` can never end the
            # test. It must fail on the assert below, not spin for ever.
            if events.count("warm") > 3:
                raise KeyboardInterrupt
            raise RuntimeError("warm on fire")

        monkeypatch.setattr(wc.turn_frames, "warm", boom)
        self._usable_sensor(wc, monkeypatch)
        self._listen_recorder(wc, events, [""])
        with caplog.at_level("WARNING"):
            self._run(wc)
        # Two passes, and each ran its capture despite the raise. The loop's
        # outer except would have skipped the capture and slept the cooldown.
        assert events == ["warm", "listen", "warm", "listen"]
        assert any("turn frame warm failed" in r.getMessage()
                   for r in caplog.records)
        # the outer handler did not run, so the error was not swallowed there
        assert not [c for c in wc._metric_counts
                    if c[0] == "halloween_error"]

    def test_no_usable_sensor_means_no_warm_and_the_capture_still_runs(
            self, wc, monkeypatch):
        # A gate that fails open passes on every loop. Warming there would hold
        # the camera open all night.
        events = []
        monkeypatch.setattr(wc.turn_frames, "warm",
                            lambda: events.append("warm"))
        monkeypatch.setattr(wc.presence, "available", lambda: False)
        monkeypatch.setattr(wc.presence, "wait_for_presence", lambda s: True)
        self._listen_recorder(wc, events, ["", ""])
        self._run(wc)
        assert events == ["listen"] * 3

    def test_a_usable_sensor_means_warm_runs(self, wc, monkeypatch):
        events = []
        monkeypatch.setattr(wc.turn_frames, "warm",
                            lambda: events.append("warm"))
        self._usable_sensor(wc, monkeypatch)
        self._listen_recorder(wc, events, ["", ""])
        self._run(wc)
        assert events == ["warm", "listen"] * 3

    def test_a_raising_available_does_not_stop_the_capture(
            self, wc, monkeypatch):
        # available() sits inside the guard for this reason. Bounded: without
        # the guard the capture never runs, and it is the test's only exit.
        events = []

        def boom():
            events.append("available")
            if events.count("available") > 3:
                raise KeyboardInterrupt
            raise RuntimeError("sensor on fire")

        monkeypatch.setattr(wc.presence, "available", boom)
        monkeypatch.setattr(wc.presence, "wait_for_presence", lambda s: True)
        self._listen_recorder(wc, events, [""])
        self._run(wc)
        assert events == ["available", "listen", "available", "listen"]

    def test_warm_runs_once_per_pass_before_the_capture(self, wc, monkeypatch):
        events = []
        monkeypatch.setattr(wc.turn_frames, "warm",
                            lambda: events.append("warm"))
        self._usable_sensor(wc, monkeypatch)
        self._listen_recorder(wc, events, ["", ""])
        self._run(wc)
        assert events == ["warm", "listen"] * 3

    def test_a_closed_gate_makes_no_warm_call(self, wc, monkeypatch):
        events = []
        monkeypatch.setattr(wc.turn_frames, "warm",
                            lambda: events.append("warm"))
        waits = []

        def closed(slice_s):
            waits.append(slice_s)
            if len(waits) > 3:
                raise KeyboardInterrupt
            return False

        monkeypatch.setattr(wc.presence, "wait_for_presence", closed)
        self._listen_recorder(wc, events, [])
        self._run(wc)
        assert len(waits) == 4
        assert events == []

    def test_warm_runs_only_after_the_gate_opens(self, wc, monkeypatch):
        events = []
        monkeypatch.setattr(wc.turn_frames, "warm",
                            lambda: events.append("warm"))
        answers = [False, False, True]

        def gate(slice_s):
            events.append("gate")
            if not answers:
                raise KeyboardInterrupt
            return answers.pop(0)

        monkeypatch.setattr(wc.presence, "available", lambda: True)
        monkeypatch.setattr(wc.presence, "wait_for_presence", gate)
        self._listen_recorder(wc, events, [""])
        self._run(wc)
        assert events == ["gate", "gate", "gate", "warm", "listen",
                          "gate"]


# ---------------------------------------------------------------------------
# The suite writes nothing under the repo's logs/
# ---------------------------------------------------------------------------

def _snapshot(path):
    out = {}
    for base, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(base, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[p] = (st.st_mtime_ns, st.st_size)
    return out


class TestTheSuiteWritesNothingUnderRepoLogs:
    def test_a_full_session_leaves_the_repo_logs_untouched(self, rig):
        repo_logs = os.path.join(REPO, "logs")
        before = _snapshot(repo_logs)
        rig.session.start()
        rig.session.handle_turn("one")
        rig.session.handle_turn("two")
        rig.session.end("timeout")
        assert _snapshot(repo_logs) == before
        assert not os.path.abspath(turn_frames.frames_dir()).startswith(
            os.path.abspath(repo_logs) + os.sep)
        assert turn_frames.frames_dir().startswith(str(rig.root))
