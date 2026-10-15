"""The session loop must read the remote end-session note during the IDLE wait.

THE BUG THIS LOCKS DOWN
-----------------------
``ConversationSession.handle_turn`` checks ``cfg.end_session_file`` at its top,
and again after playback. Neither fires while Bender is sitting in the idle
capture loop: on an empty capture the session loop did

    text = stt.listen_and_transcribe(...)
    if not text:
        if rec_start - last_heard > cfg.silence_timeout: ... break
        continue

so it went straight back into ``listen_and_transcribe`` without ever looking at
the floor. ``stt.py`` has no awareness of the note either. The visible effect:
the sidebar END button and entering puppet mode both appeared to do NOTHING,
because the only way out was the silence timeout -- and a noisy room resets
that timeout on every capture that returns text, so a session could outlive the
request indefinitely while hallucinating a reply to each door slam.

WHY THIS TEST IS STRUCTURAL
---------------------------
The loop lives inside ``main()``, which owns the Hailo, the LEDs, the mic and
systemd notification. Driving it would mean faking all of that to assert one
``if``. So this walks the AST instead and asserts the ORDER of the statements,
which is the whole property: the check has to sit between the capture returning
and the ``if not text`` branch, or the idle path still skips it.

An AST walk, not a string match: ``tests/test_stt_onset.py`` already asserts a
call site by exact source text, and that breaks on reformatting. Matching the
shape survives renames of locals and any amount of re-wrapping.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

_SRC = os.path.join(os.path.dirname(__file__), "..", "scripts", "wake_converse.py")


def _names(node) -> set:
    """Every identifier and attribute name appearing under ``node``."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _session_loop_body():
    """The body of the `while True:` that performs the conversation captures."""
    tree = ast.parse(open(_SRC).read())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.While)
                and isinstance(node.test, ast.Constant) and node.test.value is True):
            continue
        # The capture must be a DIRECT statement of this loop, and an
        # assignment. The outer wake loop and the halloween loop both wrap this
        # one in a `try:`, so "the name appears somewhere inside the body"
        # matches all three and ast.walk hands back the outermost.
        captures = any(isinstance(s, ast.Assign)
                       and "listen_and_transcribe" in _names(s.value)
                       for s in node.body)
        if not captures:
            continue
        if any("silence_timeout" in _names(s) for s in node.body):
            return node.body
    raise AssertionError("could not find the household session loop in wake_converse.py")


def _index_of(body, predicate):
    for i, stmt in enumerate(body):
        if predicate(stmt):
            return i
    return -1


class TestTheIdleCapturePathChecksTheNote:

    def test_the_loop_is_found(self):
        body = _session_loop_body()
        assert body, "no statements in the session loop"

    def test_check_sits_between_the_capture_and_the_empty_branch(self):
        body = _session_loop_body()

        capture = _index_of(body, lambda s: "listen_and_transcribe" in _names(s))
        assert capture >= 0, "no listen_and_transcribe call in the loop"

        empty = _index_of(
            body,
            lambda s: isinstance(s, ast.If)
            and isinstance(s.test, ast.UnaryOp)
            and isinstance(s.test.op, ast.Not)
            and "text" in _names(s.test),
        )
        assert empty >= 0, "no `if not text:` branch in the loop"

        check = _index_of(
            body,
            lambda s: isinstance(s, ast.If) and "end_session_file" in _names(s.test),
        )
        assert check >= 0, (
            "the session loop never checks cfg.end_session_file between captures; "
            "END and puppet mode will not stop a live session until the silence "
            "timeout happens to fire"
        )
        assert capture < check < empty, (
            "the end-session check must run AFTER the capture returns and BEFORE "
            f"the `if not text` branch (capture={capture} check={check} empty={empty}); "
            "placed after the empty branch, the idle path `continue`s past it"
        )

    def test_the_check_ends_the_session_and_does_not_continue(self):
        body = _session_loop_body()
        check = body[_index_of(
            body, lambda s: isinstance(s, ast.If) and "end_session_file" in _names(s.test))]
        inner = _names(check)
        assert "end" in inner, "the branch must call session.end(...)"
        assert any(isinstance(n, ast.Break) for n in ast.walk(check)), (
            "the branch must break out of the session loop, not continue into "
            "another capture"
        )

    def test_the_check_clears_the_abort_note_too(self):
        """session.end() removes the session and end-session files, NOT the abort file.

        ``_check_abort_on_chunk`` polls ``cfg.abort_file`` at ~10Hz during every
        playback, and the end-session route writes BOTH notes. Leaving the abort
        note behind would kill the NEXT session's greeting before a word of it
        was heard -- a far worse bug than the one being fixed.
        """
        body = _session_loop_body()
        check = body[_index_of(
            body, lambda s: isinstance(s, ast.If) and "end_session_file" in _names(s.test))]
        assert "_cleanup_abort_files" in _names(check), (
            "the branch must clear the abort note; session.end() does not"
        )


class TestTheHelpersItRelieson:

    def test_cleanup_abort_files_removes_both_notes(self, tmp_path, monkeypatch):
        """Guards the assumption the loop makes: that helper clears the abort note."""
        import importlib
        spec = importlib.util.spec_from_file_location("_wc_src", _SRC)
        # Importing wake_converse pulls in pyaudio/hailo/leds, so read the
        # helper's own source and exec just that. It is six lines and has no
        # dependencies beyond os and cfg.
        tree = ast.parse(open(_SRC).read())
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_cleanup_abort_files")
        ns = {"os": os, "cfg": type("C", (), {
            "end_session_file": str(tmp_path / ".end_session"),
            "abort_file": str(tmp_path / ".abort_playback"),
        })()}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), _SRC, "exec"), ns)

        (tmp_path / ".end_session").write_text("")
        (tmp_path / ".abort_playback").write_text("")
        ns["_cleanup_abort_files"]()
        assert not (tmp_path / ".end_session").exists()
        assert not (tmp_path / ".abort_playback").exists()

    def test_session_end_does_not_clear_the_abort_note(self, tmp_path, monkeypatch):
        """The premise of test_the_check_clears_the_abort_note_too, asserted directly.

        If ConversationSession.end() ever starts clearing the abort file, the
        loop's extra call becomes redundant rather than wrong -- but this test
        is what tells a future reader which of the two to delete.
        """
        import session as session_mod
        src = ast.parse(open(session_mod.__file__).read())
        end_fn = None
        for cls in src.body:
            if isinstance(cls, ast.ClassDef) and cls.name == "ConversationSession":
                for n in cls.body:
                    if isinstance(n, ast.FunctionDef) and n.name == "_remove_session_file":
                        end_fn = n
        assert end_fn is not None, "ConversationSession._remove_session_file not found"
        assert "abort_file" not in _names(end_fn), (
            "_remove_session_file now clears the abort note; the session loop's "
            "own _cleanup_abort_files() call is redundant and should be removed"
        )
