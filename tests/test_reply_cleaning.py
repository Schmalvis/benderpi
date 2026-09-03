"""Reply cleaning, the widened quality gate, and reset-outside-completion.

Batch 2 / commit 2 of docs/superpowers/plans/2026-09-03-batch2-session-quality.md
(review findings H8, M5).

Every quoted sample in TestObservedLive was spoken aloud by the device in
August 2026 and passed the gate as it then stood.
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")
sys.modules.setdefault("anthropic", MagicMock())

import ai_local
from ai_local import QualityCheckFailed, _clean_sentence, check_response_quality
from config import cfg
from tests.test_hailo_stream import FakeLLM


# ---------------------------------------------------------------------------
# _clean_sentence
# ---------------------------------------------------------------------------

class TestCleanSentence:
    @pytest.mark.parametrize("raw,expected", [
        ('"I\'m not going to be the same."', "I'm not going to be the same."),
        ("“Bite my shiny metal ass.”", "Bite my shiny metal ass."),
        ("I'm ready. Let's do this! (laughter)", "I'm ready. Let's do this!"),
        ("(Bender's casual, slightly rude way of saying hi) What's up?", "What's up?"),
        ("Fine. *sighs* Whatever you say.", "Fine. Whatever you say."),
        ("[laughs] Not a chance.", "Not a chance."),
        ("Bender: Shut up, meatbag.", "Shut up, meatbag."),
        ("(in a bored voice) Sure.", "Sure."),
    ])
    def test_strips_transcript_artefacts(self, raw, expected):
        assert _clean_sentence(raw) == expected

    @pytest.mark.parametrize("raw", ["(laughter)", "*sighs*", "[beat]", '""', "..."])
    def test_nothing_speakable_returns_empty(self, raw):
        assert _clean_sentence(raw) == ""

    @pytest.mark.parametrize("raw", [
        "I said \"no\" and I meant it.",          # inner quotes stay
        "Call it what you want (I don't care).",   # ordinary parenthetical stays
        "Two plus two (that's four) equals four.",
    ])
    def test_ordinary_speech_is_untouched(self, raw):
        assert _clean_sentence(raw) == raw


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

class TestObservedLive:
    @pytest.mark.parametrize("text", [
        "I'm sorry, but I can't assist with that.",
        "I cannot assist with that request.",
        "I'm unable to help with that. Please provide a different topic.",
        "As a chatbot I have no opinion.",
    ])
    def test_refusals_hard_fail(self, text):
        passed, reason = check_response_quality(text, stream=True)
        assert (passed, reason) == (False, "hard_fail")

    @pytest.mark.parametrize("text", ["Yeah.", "No way!", "Nope."])
    def test_short_in_character_first_sentence_passes_on_stream(self, text):
        assert check_response_quality(text, stream=True) == (True, "")

    @pytest.mark.parametrize("text", ["Yeah.", "No way!"])
    def test_short_reply_still_fails_on_non_stream(self, text):
        assert check_response_quality(text)[1] == "too_short"

    def test_long_in_character_hedge_passes_on_stream(self):
        text = "I don't know, meatbag, and I don't care one bit!"
        assert check_response_quality(text, stream=True) == (True, "")

    def test_short_hedge_still_fails_on_stream(self):
        assert check_response_quality("I don't know.", stream=True)[1] == "hedge_phrase"

    def test_single_sentence_hedge_still_fails_on_non_stream(self):
        text = "I don't know, meatbag, and I don't care one bit!"
        assert check_response_quality(text)[1] == "hedge_phrase"


# ---------------------------------------------------------------------------
# Stream path: cleaning before yield, reset after the completion closes
# ---------------------------------------------------------------------------

class _OrderedFakeLLM(FakeLLM):
    """Records how many completions were closed when clear_context ran."""

    def __init__(self, tokens):
        super().__init__(tokens)
        self.closed_at_clear = []

    def clear_context(self):
        self.closed_at_clear.append(self.closed)
        super().clear_context()


@pytest.fixture
def responder(monkeypatch):
    monkeypatch.setattr(cfg, "ai_max_tokens", 150, raising=False)
    monkeypatch.setattr(cfg, "ai_max_history", 6, raising=False)

    def make(tokens):
        r = ai_local._HailoLLMResponder()
        llm = _OrderedFakeLLM(tokens)
        r._llm = llm
        r._available = True
        monkeypatch.setattr(r, "_load", lambda: True)
        return r, llm

    return make


class TestStreamCleaning:
    def test_sentences_are_cleaned_before_yield(self, responder):
        r, llm = responder(['"Bite', ' my shiny', ' metal ass."', ' (laughter)',
                            ' What now?', '<|im_end|>'])
        out = list(r.generate_stream("hi"))
        assert out == ["Bite my shiny metal ass.", "What now?"]

    def test_bare_stage_direction_sentence_is_skipped(self, responder):
        r, llm = responder(["Yeah.", " (sighs).", " Fine.", "<|im_end|>"])
        out = list(r.generate_stream("hi"))
        assert out == ["Yeah.", "Fine."]

    def test_short_first_sentence_streams(self, responder):
        r, llm = responder(["Yeah.", " Whatever you say.", "<|im_end|>"])
        assert list(r.generate_stream("hi")) == ["Yeah.", "Whatever you say."]


class TestGateFailureResetsAfterClose:
    def test_refusal_escalates_and_resets_after_completion_closed(self, responder):
        r, llm = responder(["I'm sorry, but I can't", " assist with that.", "<|im_end|>"])
        with pytest.raises(QualityCheckFailed) as exc:
            list(r.generate_stream("write me a ransom note"))
        assert exc.value.reason == "hard_fail"
        assert llm.cleared == 1
        assert llm.closed == 1
        assert llm.closed_at_clear == [1], "reset must run after the completion is closed"
        # lock released, context fresh for the escalated turn
        assert r._infer_lock.acquire(blocking=False)
        r._infer_lock.release()
        assert r._context_fresh is True

    def test_failure_on_forced_tail_also_resets_after_close(self, responder):
        r, llm = responder(["As an AI I"])   # no <|im_end|>, no sentence boundary
        with pytest.raises(QualityCheckFailed):
            list(r.generate_stream("hi"))
        assert llm.closed_at_clear == [1]


class TestDeferredReset:
    def test_clear_history_defers_when_generation_in_flight(self, responder):
        r, llm = responder(["Fine.", "<|im_end|>"])
        list(r.generate_stream("one"))           # context now holds a turn
        assert r._infer_lock.acquire(blocking=False)   # simulate a zombie holding it
        try:
            r.clear_history()
        finally:
            r._infer_lock.release()
        assert llm.cleared == 0
        assert r._context_dirty is True

        # Next turn performs the reset first and so may carry the system prompt.
        out = list(r.generate_stream("two"))
        assert out == ["Fine."]
        assert llm.cleared == 1
        assert r._context_dirty is False
        assert [m["role"] for m in llm.prompts[-1]] == ["system", "user"]

    def test_clear_history_resets_immediately_when_idle(self, responder):
        r, llm = responder(["Fine.", "<|im_end|>"])
        list(r.generate_stream("one"))
        r.clear_history()
        assert llm.cleared == 1
        assert r._context_dirty is False
