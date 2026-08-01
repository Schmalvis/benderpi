"""Tests for tts_generate._sanitize_for_speech.

Piper pronounces whatever it is handed. ":)" becomes "colon, close paren" and a
chat-template token gets spelled out. The LLMs have no idea their output is
spoken rather than rendered, so this is the last line of defence before audio.

Every case in TestObservedLive is real text Bender said out loud on 2026-08-01
(session 5cfc5cd8, turn 4) — Qwen emitted an emoji, a JSON emoji blob,
"<tool_call>", and a hallucinated "<|im_start|>user" turn, and all of it was
pronounced. The rest guard the two ways a sanitiser fails: leaving junk in, and
eating real speech.
"""
import sys, os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import tts_generate
from tts_generate import _sanitize_for_speech as clean


class TestObservedLive:
    def test_truncates_at_hallucinated_turn(self):
        out = clean("Yeah whatever, meatbag.<|im_start|>user\nWhat happened when")
        assert out == "Yeah whatever, meatbag."

    def test_truncates_at_tool_call(self):
        out = clean("Fine, I'll do it.<tool_call>\n{\"name\": \"x\"}")
        assert out == "Fine, I'll do it."

    def test_strips_emoji_json_blob(self):
        out = clean("How are you today? [{'type': 'emoji', 'emoji': ':)'}]")
        assert out == "How are you today?"

    def test_strips_emoji(self):
        assert clean("Bite my shiny metal ass \U0001F602") == "Bite my shiny metal ass"

    def test_full_turn_4_payload(self):
        raw = ("I'm going to do the \"damn\" thing again. How are you today? "
               "\U0001F602\n[{'type': 'emoji', 'emoji': ':)'}]<tool_call>\n"
               "<|im_start|>user\nWhat happened when Bender asked a stranger")
        out = clean(raw)
        assert out == "I'm going to do the \"damn\" thing again. How are you today?"
        for bad in ("<|", "tool_call", "emoji", "\U0001F602"):
            assert bad not in out


class TestEmoticons:
    @pytest.mark.parametrize("emo", [":)", ":-)", ";)", ":D", "=]", ":-("])
    def test_strips_emoticons(self, emo):
        out = clean(f"Nice one {emo} good")
        assert emo not in out
        assert "Nice one" in out and "good" in out

    @pytest.mark.parametrize("keep", ["3:30", "8:1", "a ratio of 16:9"])
    def test_keeps_times_and_ratios(self, keep):
        """The emoticon pattern must not eat digits either side of a colon."""
        assert keep.split()[-1] in clean(f"It is {keep} now")


class TestKeepsRealSpeech:
    @pytest.mark.parametrize("text", [
        "Bite my shiny metal ass!",
        "It's Saturday, July 25. Another day of dealing with you humans.",
        "I have no opinion on the matter.",
        "Well, well, well - look who it is.",
        "You want the weather? Fine. It's 12 degrees and raining.",
    ])
    def test_normal_replies_pass_through_unchanged(self, text):
        assert clean(text) == text

    def test_apostrophes_and_quotes_survive(self):
        t = 'He said "no" and I don\'t care.'
        assert clean(t) == t


class TestEmptyResult:
    def test_all_emoji_returns_empty(self):
        assert clean("\U0001F602\U0001F97A") == ""

    def test_punctuation_only_returns_empty(self):
        assert clean("... , !") == ""

    def test_empty_input_returns_empty(self):
        assert clean("") == ""

    def test_preprocess_substitutes_fallback_not_silence(self):
        """An emptied reply must still make a sound — silence reads as a hang."""
        assert tts_generate._preprocess_text("\U0001F602") == \
            tts_generate._EMPTY_FALLBACK

    def test_preprocess_leaves_good_text_alone(self):
        assert tts_generate._preprocess_text("Bite my ass.") == "Bite my ass."


class TestObservability:
    def test_sanitising_emits_a_metric(self, monkeypatch):
        """A silent scrubber hides how often the model misbehaves."""
        seen = []
        monkeypatch.setattr(tts_generate.metrics, "count",
                            lambda name, **kw: seen.append((name, kw)))
        clean("Hello there <tool_call>junk")
        assert seen and seen[0][0] == "tts_sanitized"

    def test_clean_text_emits_nothing(self, monkeypatch):
        seen = []
        monkeypatch.setattr(tts_generate.metrics, "count",
                            lambda name, **kw: seen.append(name))
        clean("Bite my shiny metal ass!")
        assert seen == []


class TestQualityGateRejectsControlTokens:
    """The sanitiser stops garbage being *pronounced*; the quality gate stops it
    being the answer at all. Without this, a derailed local reply gets truncated
    to its first clean clause and spoken as if it were a real response, instead
    of escalating to the cloud for one that actually answers the question."""

    def test_control_token_reply_fails_quality(self):
        from ai_local import check_response_quality
        ok, reason = check_response_quality(
            "I'm going to do the damn thing again.<tool_call>{\"name\":\"x\"}")
        assert ok is False
        assert reason == "control_tokens"

    def test_hallucinated_turn_fails_quality(self):
        from ai_local import check_response_quality
        ok, reason = check_response_quality(
            "How are you today?<|im_start|>user\nWhat happened when")
        assert ok is False
        assert reason == "control_tokens"

    def test_clean_reply_still_passes(self):
        from ai_local import check_response_quality
        ok, reason = check_response_quality(
            "Bite my shiny metal ass, meatbag. I'm busy.")
        assert ok is True
        assert reason == ""
