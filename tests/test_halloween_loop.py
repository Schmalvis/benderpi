"""Execute the Halloween loops against fakes.

The first version of these loops shipped with 22 tests, none of which ran
them. A review then found, by running them, that: the systemd watchdog was
never fed (so systemd restarted the service every ~120s), any non-RuntimeError
killed the process and leaked the session, and the idle-clock retry was dead
code. All three passed the original suite.

These tests drive `_halloween_loop` and `_halloween_turn_loop` directly.
"""
import sys
import time
import types

import pytest

from tests.test_wake_loop_heartbeat import _patch_all_deps


def _wait_until(pred, timeout_s=2.0):
    """Give a real thread a moment, without a fixed sleep."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def wc(monkeypatch):
    """wake_converse with every hardware dependency faked, in Halloween mode."""
    fake_audio, fake_cfg, _ = _patch_all_deps(monkeypatch)
    fake_cfg.halloween_mode = True
    fake_cfg.halloween_cooldown_s = 0.0
    fake_cfg.halloween_max_turns = 3
    fake_cfg.halloween_idle_timeout_s = 6.0
    sys.modules.pop("wake_converse", None)
    import wake_converse as mod

    monkeypatch.setattr(mod, "cfg", fake_cfg, raising=False)
    monkeypatch.setattr(mod, "audio", fake_audio, raising=False)
    monkeypatch.setattr(mod, "halloween_enabled", lambda: True, raising=False)
    counts = []
    monkeypatch.setattr(mod, "metrics", types.SimpleNamespace(
        count=lambda name, **kw: counts.append((name, kw)),
        _write=lambda *a, **k: None), raising=False)
    monkeypatch.setattr(mod, "leds", types.SimpleNamespace(
        set_listening=lambda *a: None, set_talking=lambda *a: None,
        all_off=lambda *a: None, set_alert_flash=lambda *a: None), raising=False)
    monkeypatch.setattr(mod, "SessionLogger", lambda: None, raising=False)
    # NOT mod.time.sleep: the heartbeat thread sleeps on the same module, and
    # a no-op turns it into a busy spin. Patch only the cooldown.
    mod.halloween_cooldown_s = 0.0
    mod._metric_counts = counts
    return mod


class _Session:
    """Records lifecycle calls, so a leak is visible."""

    def __init__(self, results=None, raise_on_turn=None):
        self.started = False
        self.ended = []
        self.turns = []
        self._results = list(results or [])
        self._raise = raise_on_turn

    def start(self):
        self.started = True

    def handle_turn(self, text):
        self.turns.append(text)
        if self._raise is not None and len(self.turns) == 1:
            raise self._raise
        if self._results:
            return self._results.pop(0)
        return types.SimpleNamespace(should_end=False, end_reason=None)

    def end(self, reason="timeout"):
        self.ended.append(reason)


def _drive(wc, heard, session, stop_after=1):
    """Run _halloween_loop over a script of what was heard, then stop.

    The real loop is `while True` by design -- it must survive an evening -- so
    the script ends by raising KeyboardInterrupt from the fake microphone,
    which is the one exception the loop re-raises rather than swallowing.
    Without that the harness hangs, as the first version of it did.
    """
    sessions = []
    heard = list(heard)

    def listen(after_playback=True):
        if not heard:
            raise KeyboardInterrupt      # end of script
        return heard.pop(0)

    def make_session(**kw):
        sessions.append(session)
        return session

    wc.stt = types.SimpleNamespace(listen_and_transcribe=listen,
                                   release=lambda: None)
    wc.ConversationSession = make_session
    with pytest.raises((KeyboardInterrupt, IndexError, SystemExit)):
        wc._halloween_loop(None, object(), object(), wc.log)
    return sessions


class TestWatchdogHeartbeat:
    """The unit is Type=notify with WatchdogSec=120 and the only feeder was
    wait_for_wakeword(), which this mode never calls."""

    def test_the_loop_stamps_progress(self, wc):
        wc._last_progress = 0.0
        session = _Session()
        _drive(wc, ["trick or treat"], session)
        assert wc._progress_age() < 5.0, "the loop must stamp progress"

    def test_the_turn_loop_stamps_progress_too(self, wc):
        """A single turn can legitimately take 45s; the top of the loop is not
        enough on its own."""
        wc.stt = types.SimpleNamespace(listen_and_transcribe=lambda **k: "")
        session = _Session()
        wc._last_progress = 0.0
        wc._halloween_turn_loop(session, "hello", wc.log)
        assert wc._progress_age() < 5.0

    def test_the_heartbeat_pings_while_progress_is_fresh(self, wc, monkeypatch):
        """Starts the REAL thread. An earlier version of this test
        re-implemented the loop body inline, and a mutation that made the
        heartbeat unconditional passed it."""
        fed = []
        monkeypatch.setattr(wc, "_feed_watchdog", lambda: fed.append(1))
        wc._note_progress()
        _, stop = wc._start_watchdog_heartbeat(stall_limit_s=60.0, interval_s=0.01)
        try:
            _wait_until(lambda: len(fed) >= 2)
            assert len(fed) >= 2, "a healthy loop must be covered"
        finally:
            stop.set()

    def test_the_heartbeat_withholds_pings_when_the_loop_is_wedged(self, wc, monkeypatch):
        """A blind pinger would keep a hung process alive forever, which is
        exactly what WatchdogSec exists to prevent. This is the one property
        that makes the fix safe rather than just quiet."""
        fed = []
        monkeypatch.setattr(wc, "_feed_watchdog", lambda: fed.append(1))
        errors = []
        monkeypatch.setattr(wc.log, "error",
                            lambda *a, **k: errors.append(a), raising=False)
        _, stop = wc._start_watchdog_heartbeat(stall_limit_s=60.0, interval_s=0.01)
        try:
            # Wedge the loop AFTER start: startup legitimately stamps progress.
            wc._last_progress = wc.time.monotonic() - 500.0
            _wait_until(lambda: bool(errors))
            fed.clear()                      # ignore pings from before the wedge
            _wait_until(lambda: len(fed) > 0, timeout_s=0.3)
            assert fed == [], "a wedged loop must stop being covered"
            assert errors, "and it must say so in the log"
        finally:
            stop.set()

    def test_the_thread_can_be_stopped(self, wc, monkeypatch):
        """Leaked heartbeat threads from one test polluted another, which is
        how the wedge test came to pass for the wrong reason."""
        fed = []
        monkeypatch.setattr(wc, "_feed_watchdog", lambda: fed.append(1))
        thread, stop = wc._start_watchdog_heartbeat(stall_limit_s=60.0,
                                                    interval_s=0.01)
        _wait_until(lambda: len(fed) >= 1)
        stop.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    def test_the_stall_limit_sits_under_the_systemd_deadline(self, wc):
        assert wc.PROGRESS_STALL_LIMIT_S < 120
        assert wc.HEARTBEAT_INTERVAL_S * 2 < wc.PROGRESS_STALL_LIMIT_S


class TestErrorsDoNotEndTheEvening:
    @pytest.mark.parametrize("exc", [
        ValueError("bad wav header"),
        OSError("portaudio blew up"),
        KeyError("missing index key"),
        AttributeError("handler bug"),
    ])
    def test_an_ordinary_error_is_survived_and_the_session_closed(self, wc, exc):
        """Previously only RuntimeError was caught, so any of these killed the
        process AND left the session open, the LEDs on and audio held."""
        session = _Session(raise_on_turn=exc)
        _drive(wc, ["hello", "hello"], session, stop_after=2)
        assert "error" in session.ended, \
            f"the session must be closed on the error path: {session.ended}"
        names = [n for n, _ in wc._metric_counts]
        assert "halloween_error" in names

    def test_a_mic_stall_exits_for_a_restart_rather_than_spinning(self, wc):
        """A wedged mic is not recoverable by looping; only a restart re-runs
        the XVF3800 recovery. It must also use the metric the watchdog alerts
        on, or nothing tells the owner."""
        session = _Session(raise_on_turn=RuntimeError("mic stalled: no frame"))
        _drive(wc, ["hello"], session)
        names = [n for n, _ in wc._metric_counts]
        assert "wake_loop_stall_exit" in names
        assert session.ended == ["mic_stall"]


class TestSessionLifecycle:
    def test_the_greeting_plays_before_any_turn(self, wc):
        session = _Session()
        _drive(wc, ["trick or treat"], session)
        assert session.started is True
        assert session.turns == ["trick or treat"]

    def test_max_turns_is_respected_exactly(self, wc):
        session = _Session()
        wc.stt = types.SimpleNamespace(
            listen_and_transcribe=lambda **k: "and another thing")
        wc._halloween_turn_loop(session, "first", wc.log)
        assert len(session.turns) == 3        # halloween_max_turns
        assert session.ended == ["max_turns"]

    def test_a_dismissal_ends_the_session(self, wc):
        session = _Session(results=[types.SimpleNamespace(
            should_end=True, end_reason="dismissal")])
        wc.stt = types.SimpleNamespace(listen_and_transcribe=lambda **k: "")
        wc._halloween_turn_loop(session, "bye", wc.log)
        assert session.ended == ["dismissal"]

    def test_silence_ends_the_session(self, wc):
        session = _Session()
        wc.stt = types.SimpleNamespace(listen_and_transcribe=lambda **k: "")
        wc._halloween_turn_loop(session, "hello", wc.log)
        assert session.ended == ["timeout"]

    def test_a_crash_inside_the_turn_loop_still_closes_the_session(self, wc):
        """The leak the review found: the session held the audio device and
        left the LEDs on until the process was restarted."""
        session = _Session(raise_on_turn=ValueError("boom"))
        _drive(wc, ["hello"], session)
        assert "error" in session.ended

    def test_empty_capture_is_not_treated_as_speech(self, wc):
        session = _Session()
        sessions = _drive(wc, ["", "", "trick or treat"], session, stop_after=1)
        # three listens, one session: the empty ones must not start a session
        assert session.turns == ["trick or treat"]


class TestIdleClockRetry:
    """The retry exists so a child who pauses to think is not cut off. With the
    clock anchored on `now` it was unreachable, because an empty capture has
    itself already consumed the onset timeout."""

    def test_a_second_capture_is_attempted_after_one_empty_window(self, wc, monkeypatch):
        calls = []

        # A slow empty capture: 7.0s elapse DURING the capture, longer than
        # the 6.0s idle timeout. That is the normal case, not an edge case --
        # an empty capture waits stt_speech_onset_timeout_s (6.0s) by
        # definition. Anchored on `now` the check was therefore always true
        # and the retry below was unreachable.
        clock = [1000.0]
        monkeypatch.setattr(wc.time, "monotonic", lambda: clock[0])

        def listen(after_playback=True):
            calls.append(after_playback)
            clock[0] += 7.0
            return "" if len(calls) == 1 else "I was thinking"

        wc.stt = types.SimpleNamespace(listen_and_transcribe=listen)
        session = _Session()
        wc._halloween_turn_loop(session, "hello", wc.log)
        assert len(calls) >= 2, "the retry must fire for a slow empty capture"
        assert "I was thinking" in session.turns


class TestItIsActuallyWiredIn:
    """The original review found a response bank that nothing read and a
    heartbeat that nothing started. A feature that works but is never called
    is the failure mode this project keeps hitting, so wiring gets its own
    checks -- and they inspect the compiled function's referenced names, not
    the source text.
    """

    def test_main_starts_the_heartbeat(self, wc):
        assert "_start_watchdog_heartbeat" in wc.main.__code__.co_names

    def test_main_reaches_the_halloween_loop(self, wc):
        assert "_halloween_loop" in wc.main.__code__.co_names
        assert "halloween_enabled" in wc.main.__code__.co_names

    def test_the_loops_stamp_progress(self, wc):
        for fn in (wc._halloween_loop, wc._halloween_turn_loop):
            assert "_note_progress" in fn.__code__.co_names, fn.__name__

    def test_the_wake_loop_still_feeds_the_watchdog_directly(self, wc):
        """Belt and braces: the heartbeat does not replace the wake loop's own
        pings, it covers the mode that had none."""
        assert "_feed_watchdog" in wc.wait_for_wakeword.__code__.co_names


class TestItRefusesToRunDegraded:
    """With no local model, the responder's routing falls back to cloud_only.
    Halloween mode passes None as the cloud responder, so every unmatched turn
    spoke a raw error line to a child, bypassing the curated fallbacks."""

    def test_main_checks_for_the_local_model(self, wc):
        assert "_halloween_loop" in wc.main.__code__.co_names
        src = wc.main.__code__
        # the refusal must be counted, so STATUS.md and the owner can see it
        assert "halloween_refused" in [
            c for c in src.co_consts if isinstance(c, str)]

    def test_the_refusal_falls_through_to_the_wake_word_loop(self, wc):
        """Not a crash and not silence: household mode still answers the bank
        from a WAV, it just requires the wake word."""
        import dis
        names = [i.argval for i in dis.get_instructions(wc.main)]
        assert "wait_for_wakeword" in names


class TestTheHouseholdLoopIsCoveredToo:
    """Only wait_for_wakeword() ever fed the watchdog, so nothing covered a
    household session either. A 3-turn session at the measured 7-19s per turn,
    plus captures, can pass WatchdogSec=120 and be killed mid-reply."""

    def test_the_household_loop_stamps_progress(self, wc):
        assert "_note_progress" in wc.main.__code__.co_names

    def test_both_modes_share_one_heartbeat(self, wc):
        """Started before the mode branch, so neither mode can miss it."""
        import dis
        names = [i.argval for i in dis.get_instructions(wc.main)]
        assert names.index("_start_watchdog_heartbeat") < names.index("_halloween_loop")
