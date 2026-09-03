"""Decode control on the Hailo stream path: sampling kwargs, sentence cap,
derail detection on every sentence, and the cap-hit tail rule.

Batch 2 / commit 3 of docs/superpowers/plans/2026-09-03-batch2-session-quality.md
(review findings H7, H9).
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")
sys.modules.setdefault("anthropic", MagicMock())

import ai_local
from ai_local import QualityCheckFailed
from config import cfg
from tests.test_hailo_stream import FakeLLM, _FakeCompletion


class _KwFakeLLM(FakeLLM):
    """FakeLLM that also records the sampling kwargs of each generate call."""

    def __init__(self, tokens):
        super().__init__(tokens)
        self.kwargs = []

    def generate(self, prompt=None, **kw):
        self.kwargs.append(kw)
        return super().generate(prompt=prompt, **kw)

    def generate_all(self, prompt=None, **kw):
        self.kwargs.append(kw)
        return super().generate_all(prompt=prompt, **kw)


@pytest.fixture
def responder(monkeypatch):
    monkeypatch.setattr(cfg, "ai_max_history", 6, raising=False)
    monkeypatch.setattr(cfg, "ai_temperature", 0.7, raising=False)
    monkeypatch.setattr(cfg, "ai_hailo_top_p", 0.9, raising=False)
    monkeypatch.setattr(cfg, "ai_hailo_frequency_penalty", 1.1, raising=False)
    monkeypatch.setattr(cfg, "ai_hailo_max_tokens", 80, raising=False)
    monkeypatch.setattr(cfg, "ai_max_sentences", 3, raising=False)
    monkeypatch.setattr(ai_local.metrics, "count", lambda *a, **k: None)
    monkeypatch.setattr(ai_local.metrics, "_write", lambda *a, **k: None)

    def make(tokens):
        r = ai_local._HailoLLMResponder()
        llm = _KwFakeLLM(tokens)
        r._llm = llm
        r._available = True
        monkeypatch.setattr(r, "_load", lambda: True)
        return r, llm

    return make


class TestSamplingKwargs:
    def test_stream_passes_configured_sampling(self, responder):
        r, llm = responder(["Fine.", "<|im_end|>"])
        list(r.generate_stream("hi"))
        kw = llm.kwargs[0]
        assert kw["temperature"] == 0.7
        assert kw["top_p"] == 0.9
        assert kw["frequency_penalty"] == 1.1
        assert kw["max_generated_tokens"] == 80
        assert kw["do_sample"] is True

    def test_seed_differs_between_calls(self, responder):
        r, llm = responder(["Fine.", "<|im_end|>"])
        seeds = set()
        for _ in range(5):
            list(r.generate_stream("hi"))
            seeds.add(llm.kwargs[-1]["seed"])
        assert len(seeds) > 1, "seed must not be fixed (it was 42 for every call)"
        assert all(isinstance(s, int) and s > 0 for s in seeds)

    def test_penalty_omitted_when_unset(self, responder, monkeypatch):
        monkeypatch.setattr(cfg, "ai_hailo_frequency_penalty", None, raising=False)
        r, llm = responder(["Fine.", "<|im_end|>"])
        list(r.generate_stream("hi"))
        assert "frequency_penalty" not in llm.kwargs[0]

    def test_non_stream_path_uses_same_kwargs(self, responder):
        r, llm = responder(["Bite my shiny metal ass.", "<|im_end|>"])
        r.generate("hi")
        assert llm.kwargs[0]["max_generated_tokens"] == 80
        assert llm.kwargs[0]["do_sample"] is True

    def test_ollama_options_mirror(self, monkeypatch):
        monkeypatch.setattr(cfg, "ai_hailo_frequency_penalty", 1.1, raising=False)
        opts = ai_local._ollama_options()
        assert opts["num_predict"] == 80
        assert opts["repeat_penalty"] == 1.1
        assert opts["temperature"] == 0.7


class TestSentenceCap:
    def test_stops_after_three_sentences(self, responder):
        r, llm = responder(["One.", " Two.", " Three.", " Four.", " Five.", "<|im_end|>"])
        out = list(r.generate_stream("hi"))
        assert out == ["One.", "Two.", "Three."]
        assert llm.closed == 1, "leaving the completion aborts decode"

    def test_cap_zero_disables(self, responder, monkeypatch):
        monkeypatch.setattr(cfg, "ai_max_sentences", 0, raising=False)
        r, llm = responder(["One.", " Two.", " Three.", " Four.", "<|im_end|>"])
        assert list(r.generate_stream("hi")) == ["One.", "Two.", "Three.", "Four."]

    def test_context_kept_after_cap(self, responder):
        r, llm = responder(["One.", " Two.", " Three.", " Four.", "<|im_end|>"])
        list(r.generate_stream("hi"))
        assert llm.cleared == 0
        assert r._context_fresh is False


class TestDerailDetection:
    def test_control_token_after_sentence_one_ends_turn_and_resets(self, responder):
        r, llm = responder(["Bite my shiny metal ass.", " What now?",
                            "<|im_start|>", "user", "\nWhat happened", "<|im_end|>"])
        out = list(r.generate_stream("hi"))
        assert out == ["Bite my shiny metal ass.", "What now?"]
        assert llm.cleared == 1, "a derailed context must not be kept"
        assert r._context_fresh is True

    def test_control_marker_inside_sentence_text_is_dropped(self, responder):
        # marker arrives as ordinary text tokens, not a discrete special token
        r, llm = responder(["Yeah, whatever.", " <tool", "_call>{}", "</tool_call>.", "<|im_end|>"])
        out = list(r.generate_stream("hi"))
        assert out == ["Yeah, whatever."]
        assert llm.cleared == 1

    def test_derail_on_sentence_one_escalates(self, responder):
        r, llm = responder(["<|im_start|>", "user\nhello"])
        with pytest.raises(QualityCheckFailed) as exc:
            list(r.generate_stream("hi"))
        assert exc.value.reason == "control_tokens"
        assert llm.cleared == 1
        assert llm.closed == 1

    def test_text_before_derail_without_punctuation_is_dropped(self, responder):
        r, llm = responder(["Bite me.", " And then I", "<|im_start|>", "user"])
        out = list(r.generate_stream("hi"))
        assert out == ["Bite me."]


class TestCapHitTail:
    def test_unfinished_tail_after_a_sentence_is_dropped(self, responder):
        # stream ends with no <|im_end|>: the token cap was hit mid-sentence
        r, llm = responder(["How are you today?", " [{'ty"])
        assert list(r.generate_stream("hi")) == ["How are you today?"]

    def test_finished_tail_after_a_sentence_is_kept(self, responder):
        r, llm = responder(["Bite me.", " I'm busy."])
        assert list(r.generate_stream("hi")) == ["Bite me.", "I'm busy."]

    def test_unfinished_tail_is_kept_when_nothing_was_said(self, responder):
        # something beats silence when the cap cut the only sentence
        r, llm = responder(["I have no opinion on the matter, meatbag, and"])
        assert list(r.generate_stream("hi")) == \
            ["I have no opinion on the matter, meatbag, and"]

    def test_clean_finish_flushes_tail_regardless(self, responder):
        r, llm = responder(["Bite me.", " Whatever", "<|im_end|>"])
        assert list(r.generate_stream("hi")) == ["Bite me.", "Whatever"]
