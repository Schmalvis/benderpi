"""The END button, and what it can and cannot do in Halloween mode.

The incident, 2026-10-15, from /home/pi/bender/logs/bender.log:

    19:29:18  Heard (open mic): "So, you're searching for the other one?..."
    19:29:50  owner presses END
    19:29:52  Remote end-session: abrupt exit          <- session 1 ENDED
    19:30:00  Heard (open mic): 'M-K-'                 <- session 2 starts
    19:30:29  owner presses END  -> Playback aborted   <- session 2 ENDED
    19:30:35  Heard (open mic): 'Everyone has their key.'
    19:30:37  owner presses END  -> Remote end-session <- session 3 ENDED
    19:30:44  Heard (open mic): 'Can you search up like?'

Every press worked. Every press ended a session. Halloween mode drops the wake
word, so the next sound started another one 6-8s later -- including the owner's
own voice as he reported the problem.

So there are two separate things, and this file holds the line on both:

  * END ends THE CURRENT SESSION, promptly, including during a capture already
    in flight. That is what the checks below assert.
  * END DOES NOT SILENCE BENDER in Halloween mode, and no amount of fixing the
    loop can make it. The sidebar STOP button stops bender-converse. That is
    the only thing that makes him quiet.

The checks are AST-based rather than string matches, deliberately: the same
reasoning as tests/test_wake_loop_end_session.py, because
tests/test_stt_onset.py:213 asserts a call site by exact source text and breaks
on reformatting.
"""
from __future__ import annotations

import ast
import os

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "scripts", "wake_converse.py")


@pytest.fixture(scope="module")
def tree():
    with open(_SRC, encoding="utf-8") as f:
        return ast.parse(f.read())


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in wake_converse.py")


def _checks_end_session_file(node) -> bool:
    """True when this subtree tests os.path.exists(cfg.end_session_file)."""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        if not (isinstance(n.func, ast.Attribute) and n.func.attr == "exists"):
            continue
        for a in n.args:
            if isinstance(a, ast.Attribute) and a.attr == "end_session_file":
                return True
    return False


def _captures(node):
    """Every stt.listen_and_transcribe(...) call in this subtree."""
    out = []
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "listen_and_transcribe"):
            out.append(n)
    return out


# ── Every Halloween capture must be followed by the check ───────────────────

def test_the_halloween_loop_has_exactly_one_trigger_capture(tree):
    """A guard that counts nothing would pass for the wrong reason."""
    assert len(_captures(_func(tree, "_halloween_loop"))) == 1


def test_the_halloween_turn_loop_has_two_captures(tree):
    assert len(_captures(_func(tree, "_halloween_turn_loop"))) == 2


@pytest.mark.parametrize("fn", ["_halloween_loop", "_halloween_turn_loop"])
def test_the_function_reads_the_end_session_note(tree, fn):
    """Neither read it before 2026-10-15. stt.py never reads it at all."""
    assert _checks_end_session_file(_func(tree, fn)), (
        f"{fn}() does not check cfg.end_session_file. A capture runs up to "
        "stt_speech_onset_timeout_s (6s idle) or max_record_seconds (15s), so "
        "END waits that out and then answers one more turn."
    )


def test_the_trigger_capture_check_sits_before_the_empty_text_branch(tree):
    """Order matters: an END during the capture must not open a session.

    If the check sat after `if not text: continue`, a capture that returned
    real text would start a session from speech the owner had already tried to
    stop.
    """
    fn = _func(tree, "_halloween_loop")
    body = None
    for n in ast.walk(fn):
        if isinstance(n, ast.While):
            # the loop whose body directly assigns the capture
            for st in n.body:
                if (isinstance(st, ast.Try)):
                    for inner in st.body:
                        if (isinstance(inner, ast.Assign)
                                and _captures(inner)):
                            body = st.body
            if body:
                break
    assert body is not None, "could not locate the trigger capture statement"

    capture_at = end_check_at = empty_text_at = None
    for i, st in enumerate(body):
        if isinstance(st, ast.Assign) and _captures(st):
            capture_at = i
        elif isinstance(st, ast.If) and _checks_end_session_file(st.test):
            if end_check_at is None:
                end_check_at = i
        elif (isinstance(st, ast.If) and isinstance(st.test, ast.UnaryOp)
              and isinstance(st.test.op, ast.Not)
              and isinstance(st.test.operand, ast.Name)
              and st.test.operand.id == "text"):
            empty_text_at = i

    assert capture_at is not None, "no capture assignment"
    assert end_check_at is not None, "no end-session check in the loop body"
    assert empty_text_at is not None, "no `if not text` branch"
    assert capture_at < end_check_at < empty_text_at, (
        f"order is capture={capture_at} check={end_check_at} "
        f"empty={empty_text_at}; the check must sit between them"
    )


def test_the_checks_clear_the_abort_note(tree):
    """session.end() clears session_file and end_session_file, NOT abort_file.

    _check_abort_on_chunk polls abort_file at ~10Hz, so a leftover kills the
    next session's greeting. Same trap the household loop has.
    """
    for fn_name in ("_halloween_loop", "_halloween_turn_loop"):
        fn = _func(tree, fn_name)
        names = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_cleanup_abort_files" in names, (
            f"{fn_name}() ends on the note without clearing abort_file")


def test_the_turn_loop_ends_the_session_rather_than_continuing(tree):
    """In a session, the note must end it. Not skip a turn."""
    fn = _func(tree, "_halloween_turn_loop")
    found = False
    for n in ast.walk(fn):
        if isinstance(n, ast.If) and _checks_end_session_file(n.test):
            calls = {c.func.attr for c in ast.walk(n)
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
            if "end" in calls:
                found = True
    assert found, "the note is seen but session.end() is not called"


# ── The honest limit, written down so nobody re-reports it as a bug ─────────

def test_the_open_mic_restarts_after_an_end_and_that_is_by_design(tree):
    """_halloween_loop has no 'stop' state, and this records why.

    After a session ends the loop returns to the top, waits for presence and
    captures again. Nothing carries "the owner wants quiet" across passes.
    That is deliberate: the owner's worst acceptable failure is SILENCE, so a
    latched quiet state that nobody remembers to clear is more dangerous than
    a Bender who answers the door too eagerly. The sidebar STOP button stops
    the service instead, which is visible, deliberate and reversible in a tap.

    If someone adds a pause flag later, this test fails and they must decide
    what clears it before the next child arrives.
    """
    fn = _func(tree, "_halloween_loop")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    for suspicious in ("paused", "pause", "quiet", "halted", "suspended"):
        assert suspicious not in names, (
            f"_halloween_loop now has a '{suspicious}' flag. Decide what "
            "clears it before a child arrives, then update this test."
        )
