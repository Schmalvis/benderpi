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
