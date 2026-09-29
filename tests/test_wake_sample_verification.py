"""Verifying that a captured clip actually contains the wake phrase.

2026-09-28: 115 of 130 "positive" clips did not. The prompts described the
recording condition and never printed the words, so the speaker read the
condition aloud, and openWakeWord — which labels by directory, with no
transcript — trained on "This is my normal speaking voice" as the wake word,
copied 50-88 times. Nothing downstream could see it: clean audio, good levels,
happy VAD. These tests pin the check that makes it impossible to repeat.
"""
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.modules.setdefault("pyaudio", MagicMock())

import capture_wake_samples as cap


class TestPhraseDetection:
    @pytest.mark.parametrize("text", [
        "hey bender", "Hey, Bender", "HEY BENDER!", "hey bender, what's the weather",
        "Okay so, hey bender, turn the lights on",
    ])
    def test_accepts_the_phrase(self, text):
        assert cap.phrase_present(text)

    @pytest.mark.parametrize("text", [
        # what Whisper makes of a fast or clipped "hey bender" — rejecting
        # these would send the speaker back to re-record perfectly good clips
        "Evander", "Hey, benda", "hey bande", "heybender",
    ])
    def test_accepts_plausible_mistranscriptions(self, text):
        assert cap.phrase_present(text)

    @pytest.mark.parametrize("text", [
        # the actual contents of the ruined clips
        "This is my normal speaking voice.",
        "No one speaking voice 1.5.",
        "3 meters away.",
        "facing away from the device",
        "This is Midloud, raised more.",
        "about three inches away and not going to",
        "", "[BLANK_AUDIO]", "(muffled)",
    ])
    def test_rejects_condition_narration_and_silence(self, text):
        assert not cap.phrase_present(text)

    @pytest.mark.parametrize("text", ["blender", "remember", "surrender", "lavender"])
    def test_rejects_the_hard_negatives(self, text):
        """These are deliberately close, and must never pass as positives."""
        assert not cap.phrase_present(text)


class TestKeepPrompt:
    """An uncertain transcript goes to the speaker, and the SAFE answer is the
    default: Enter re-records, only an explicit 'y' keeps the clip."""

    def test_enter_does_not_keep(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a: "")
        assert cap._prompt("keep? ", keep_yes=True) is False

    def test_y_keeps(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a: "y")
        assert cap._prompt("keep? ", keep_yes=True) is True

    def test_normal_prompt_still_continues_on_enter(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a: "")
        assert cap._prompt("record? ") is True

    def test_q_still_quits(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a: "q")
        assert cap._prompt("record? ") is False


class TestTranscriberFallback:
    def test_no_backend_returns_none_rather_than_raising(self, monkeypatch):
        """A dev clone has no Whisper. Capture must still run there, loudly
        unverified, rather than crash."""
        monkeypatch.setitem(sys.modules, "stt", None)
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
            else __builtins__.__import__

        def boom(name, *a, **k):
            if name == "stt":
                raise ImportError("no stt here")
            return real_import(name, *a, **k)

        monkeypatch.setattr("builtins.__import__", boom)
        assert cap._transcriber() is None

    def test_a_failing_transcription_returns_empty_not_an_exception(self, monkeypatch):
        fake = types.ModuleType("stt")
        fake.transcribe_file = lambda p: (_ for _ in ()).throw(RuntimeError("hailo busy"))
        monkeypatch.setitem(sys.modules, "stt", fake)
        t = cap._transcriber()
        assert t is not None
        assert t("/tmp/x.wav") == ""
        # empty transcript fails the phrase check, so the clip is queried
        assert not cap.phrase_present(t("/tmp/x.wav"))


class TestThreeWayVerdict:
    """A strict phrase match condemned 84 of 116 deliberately-recorded clips,
    because Whisper renders a 2s far-field "hey bender" as "Hey Pender",
    "Hey, Sander", "A feather" or "Ebato". The machine now rules only on what
    it can actually tell apart."""

    @pytest.mark.parametrize("text", [
        "hey bender", "Hey, Bender", "Okay, so, hey Bender, what's the...",
        "A bender, turn the lights on.", "Evander",
    ])
    def test_recognisable_phrase_is_ok(self, text):
        assert cap.classify_clip(text) == "ok"

    @pytest.mark.parametrize("text", [
        # real transcripts of real, correct recordings
        "Hey Pender!", "Hey, thunder!", "Hey, Sander.", "A-Bent.", "A feather.",
        "Ebato.", "Hey, Bantu.", "A-bendle", "It's a bit.",
    ])
    def test_short_and_mangled_is_unclear_not_wrong(self, text):
        assert cap.classify_clip(text) == "unclear"

    @pytest.mark.parametrize("text", [
        # the narration that ruined the first dataset
        "This is my normal speaking voice.",
        "about three inches away and not going to",
        "I'm finished so why it's taking a long time to",
        "facing away from the device from",
        # and genuine junk from the second capture
        "I'm in the middle of the city.",
        "Heaven, Heaven, Heaven, Heaven, Heaven, Heaven, Heaven, Heaven,",
        "", "   ",
    ])
    def test_sentences_without_the_phrase_are_wrong(self, text):
        assert cap.classify_clip(text) == "wrong"

    def test_only_wrong_blocks_a_clip(self):
        """The capture loop re-records on "wrong" and keeps the rest; the
        audit quarantines on "wrong" and keeps the rest. One rule, both
        tools, so they cannot disagree about the same clip."""
        assert cap.classify_clip("Hey Pender!") != "wrong"
        assert cap.classify_clip("This is my normal speaking voice.") == "wrong"


class TestCaptureWiring:
    """The gate was committed once WITHOUT being wired into the capture loop:
    the helpers existed, the loop never called them, and a whole capture
    session ran unverified. These pin the wiring itself."""

    def test_the_loop_calls_the_classifier(self):
        import inspect
        src = inspect.getsource(cap.capture_prompted)
        assert "classify_clip(" in src
        assert "verify is not None" in src

    def test_the_transcript_and_verdict_are_recorded(self):
        import inspect
        src = inspect.getsource(cap.capture_prompted)
        assert '"heard": heard' in src
        assert '"verdict": verdict' in src

    def test_verification_is_on_by_default(self):
        import inspect
        src = inspect.getsource(cap.capture_prompted)
        assert "not args.no_verify" in src
