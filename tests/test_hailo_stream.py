"""Tests for real Hailo token streaming and the on-chip context protocol.

The context protocol is the subtle part, and it is a genuine landmine: HailoRT
maintains conversation context ON-CHIP between generate() calls and rejects a
system-role message on any call after the first —

    [HailoRT] [error] CHECK failed - System role messages can only be provided
    on the first prompt  ->  HAILO_INVALID_OPERATION(6)

Before the models were held resident this was invisible, because the LLM object
was destroyed and rebuilt every turn, so every turn got a fresh context. Making
the models resident without changing the prompt-building broke turn 2 of every
multi-turn AI session. These tests pin the protocol down so it cannot regress.

Verified on-device 2026-07-30: system-on-every-call raises; system-once then
user-only works and the model recalls earlier turns without us resending them
(TTFT 1101ms fresh -> 371ms thereafter); clear_context() restores a fresh
context; aborting mid-stream does not wedge the LLM.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")

sys.modules.setdefault("anthropic", MagicMock())

import ai_local
from ai_local import QualityCheckFailed
from config import cfg


# ---------------------------------------------------------------------------
# Fake HailoRT LLM
# ---------------------------------------------------------------------------

class _FakeCompletion:
    """Mimics LLMGeneratorCompletion: a context manager yielding tokens."""

    def __init__(self, tokens, owner):
        self._tokens = tokens
        self._owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._owner.closed += 1
        return False

    def __iter__(self):
        for t in self._tokens:
            yield t


class FakeLLM:
    """Enforces the real HailoRT rule: a system message is only legal on a
    fresh context. Any later call carrying one raises, exactly as the chip does.
    """

    def __init__(self, tokens=None):
        self.tokens = tokens or ["Bite", " my", " shiny", " metal", " ass",
                                 "!", " What", " now", "?", "<|im_end|>"]
        self.fresh = True
        self.prompts = []
        self.cleared = 0
        self.closed = 0

    def _check(self, prompt):
        self.prompts.append(prompt)
        has_system = any(m.get("role") == "system" for m in prompt)
        if has_system and not self.fresh:
            raise RuntimeError(
                "System role messages can only be provided on the first prompt")
        self.fresh = False

    def generate(self, prompt=None, **kw):
        self._check(prompt)
        return _FakeCompletion(self.tokens, self)

    def generate_all(self, prompt=None, **kw):
        self._check(prompt)
        return "".join(self.tokens)

    def clear_context(self):
        self.cleared += 1
        self.fresh = True


@pytest.fixture
def responder(monkeypatch):
    monkeypatch.setattr(cfg, "ai_max_tokens", 150, raising=False)
    monkeypatch.setattr(cfg, "ai_max_history", 6, raising=False)
    r = ai_local._HailoLLMResponder()
    llm = FakeLLM()
    r._llm = llm
    r._available = True
    monkeypatch.setattr(r, "_load", lambda: True)
    return r, llm


# ---------------------------------------------------------------------------
# The regression this protocol exists to prevent
# ---------------------------------------------------------------------------

class TestContextProtocol:
    def test_system_prompt_sent_only_on_first_turn(self, responder):
        r, llm = responder

        list(r.generate_stream("hello"))
        list(r.generate_stream("and again"))
        list(r.generate_stream("third time"))

        roles = [[m["role"] for m in p] for p in llm.prompts]
        assert roles[0] == ["system", "user"], "first turn must carry the system prompt"
        assert roles[1] == ["user"], "later turns must NOT resend the system prompt"
        assert roles[2] == ["user"]

    def test_multi_turn_does_not_raise(self, responder):
        """Turn 2 of a session used to blow up with HAILO_INVALID_OPERATION."""
        r, llm = responder
        for _ in range(4):
            assert list(r.generate_stream("say something"))

    def test_history_is_not_resent(self, responder):
        """The chip holds the conversation; resending it wastes prompt encoding
        (measured 1101ms -> 371ms TTFT) and would break the protocol."""
        r, llm = responder
        list(r.generate_stream("first question"))
        list(r.generate_stream("second question"))

        second = llm.prompts[1]
        assert len(second) == 1
        text = second[0]["content"][0]["text"]
        assert text == "second question"
        assert "first question" not in text

    def test_non_stream_generate_uses_same_protocol(self, responder):
        r, llm = responder
        r.generate("one")
        r.generate("two")
        roles = [[m["role"] for m in p] for p in llm.prompts]
        assert roles == [["system", "user"], ["user"]]

    def test_stream_and_non_stream_share_one_context(self, responder):
        """A session can mix both paths; neither may resend the system prompt."""
        r, llm = responder
        list(r.generate_stream("streamed"))
        r.generate("not streamed")
        roles = [[m["role"] for m in p] for p in llm.prompts]
        assert roles == [["system", "user"], ["user"]]

    def test_clear_history_restores_fresh_context(self, responder):
        r, llm = responder
        list(r.generate_stream("session one"))
        r.clear_history()
        list(r.generate_stream("session two"))

        assert llm.cleared >= 1
        roles = [[m["role"] for m in p] for p in llm.prompts]
        assert roles[1] == ["system", "user"], "new session must start fresh"

    def test_context_recycled_at_history_limit(self, responder, monkeypatch):
        """On-chip context can only be cleared wholesale, so ai_max_history
        bounds it by recycling rather than trimming."""
        r, llm = responder
        monkeypatch.setattr(cfg, "ai_max_history", 3, raising=False)

        for i in range(4):
            list(r.generate_stream(f"turn {i}"))

        assert llm.cleared >= 1
        roles = [[m["role"] for m in p] for p in llm.prompts]
        assert roles[3] == ["system", "user"], "recycled context resends the system prompt"

    def test_scene_context_only_on_first_turn(self, responder):
        r, llm = responder
        r.inject_scene_context("[Room: one person]")

        list(r.generate_stream("what do you see"))
        list(r.generate_stream("anything else"))

        assert "[Room: one person]" in llm.prompts[0][0 + 1]["content"][0]["text"]
        assert "[Room: one person]" not in llm.prompts[1][0]["content"][0]["text"]


# ---------------------------------------------------------------------------
# Streaming behaviour
# ---------------------------------------------------------------------------

class TestStreaming:
    def test_yields_sentences_not_one_blob(self, responder):
        r, _ = responder
        sentences = list(r.generate_stream("hi"))
        assert len(sentences) >= 2, "must emit per sentence so TTS can start early"
        assert sentences[0] == "Bite my shiny metal ass!"

    def test_im_end_never_reaches_the_caller(self, responder):
        r, _ = responder
        sentences = list(r.generate_stream("hi"))
        assert all("<|im_end|>" not in s for s in sentences)
        assert all("<|" not in s for s in sentences)

    def test_im_end_embedded_in_a_token_is_stripped(self, monkeypatch):
        """Belt and braces: on-device it arrives as its own token, but a runtime
        that chunked differently must not make Bender say '<|im_end|>' out loud."""
        monkeypatch.setattr(cfg, "ai_max_tokens", 150, raising=False)
        r = ai_local._HailoLLMResponder()
        r._llm = FakeLLM(tokens=["All done here.", " Bye!<|im_end|>"])
        r._available = True
        monkeypatch.setattr(r, "_load", lambda: True)

        sentences = list(r.generate_stream("hi"))
        assert all("<|im_end|>" not in s for s in sentences)
        assert "Bye!" in " ".join(sentences)

    def test_trailing_partial_special_token_not_spoken(self, monkeypatch):
        monkeypatch.setattr(cfg, "ai_max_tokens", 150, raising=False)
        r = ai_local._HailoLLMResponder()
        r._llm = FakeLLM(tokens=["Fine", " whatever", "<|im_"])
        r._available = True
        monkeypatch.setattr(r, "_load", lambda: True)

        sentences = list(r.generate_stream("hi"))
        assert all("<|" not in s for s in sentences)
        assert "Fine whatever" in " ".join(sentences)

    def test_completion_closed_even_when_abandoned(self, responder):
        """Abort path: caller stops consuming, the `with` must still close."""
        r, llm = responder
        gen = r.generate_stream("hi")
        next(gen)
        gen.close()

        assert llm.closed == 1
        assert r._infer_lock.acquire(blocking=False) is True, "lock must be released"
        r._infer_lock.release()

    def test_abort_leaves_context_marked_used(self, responder):
        """An aborted turn is still committed on-chip; if we forgot that, the
        next turn would resend the system prompt and raise."""
        r, llm = responder
        gen = r.generate_stream("hi")
        next(gen)
        gen.close()

        list(r.generate_stream("next turn"))  # must not raise
        assert [m["role"] for m in llm.prompts[1]] == ["user"]

    def test_lock_released_on_exception(self, responder, monkeypatch):
        r, llm = responder

        def boom(**kw):
            raise RuntimeError("chip on fire")

        monkeypatch.setattr(llm, "generate", boom)
        with pytest.raises(RuntimeError):
            list(r.generate_stream("hi"))

        assert r._infer_lock.acquire(blocking=False) is True
        r._infer_lock.release()


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------

class TestQualityGate:
    def test_first_sentence_failure_raises_before_yielding(self, monkeypatch):
        monkeypatch.setattr(cfg, "ai_max_tokens", 150, raising=False)
        r = ai_local._HailoLLMResponder()
        r._llm = FakeLLM(tokens=["As an AI", " language model", ", I cannot.",
                                 " More text.", "<|im_end|>"])
        r._available = True
        monkeypatch.setattr(r, "_load", lambda: True)

        gen = r.generate_stream("hi")
        with pytest.raises(QualityCheckFailed):
            next(gen)

    def test_quality_failure_recycles_context(self, monkeypatch):
        """A rejected reply is still on-chip; leaving it there would poison the
        cloud-escalated turn that follows."""
        monkeypatch.setattr(cfg, "ai_max_tokens", 150, raising=False)
        r = ai_local._HailoLLMResponder()
        llm = FakeLLM(tokens=["As an AI", " I cannot help.", "<|im_end|>"])
        r._llm = llm
        r._available = True
        monkeypatch.setattr(r, "_load", lambda: True)

        with pytest.raises(QualityCheckFailed):
            list(r.generate_stream("hi"))

        assert llm.cleared >= 1
        assert r._context_fresh is True


# ---------------------------------------------------------------------------
# Failover semantics
# ---------------------------------------------------------------------------

class TestFailover:
    def test_falls_back_to_ollama_before_first_sentence(self, monkeypatch):
        r = ai_local.LocalAIResponder()

        def dead(_text):
            raise RuntimeError("no hailo")
            yield  # pragma: no cover

        monkeypatch.setattr(r._hailo, "generate_stream", dead)
        monkeypatch.setattr(r._ollama, "generate_stream",
                            lambda t: iter(["Ollama saved the day."]))

        assert list(r.generate_stream("hi")) == ["Ollama saved the day."]

    def test_no_ollama_restart_after_audio_started(self, monkeypatch):
        """Once a sentence is playing we cannot un-speak it; splicing a second
        model's voice onto the end of the first is worse than stopping."""
        r = ai_local.LocalAIResponder()

        def half(_text):
            yield "First sentence is fine."
            raise RuntimeError("chip died mid-reply")

        called = []
        monkeypatch.setattr(r._hailo, "generate_stream", half)
        monkeypatch.setattr(r._ollama, "generate_stream",
                            lambda t: called.append(1) or iter(["nope"]))

        assert list(r.generate_stream("hi")) == ["First sentence is fine."]
        assert called == [], "must not restart on Ollama mid-reply"

    def test_quality_failure_propagates_without_ollama(self, monkeypatch):
        r = ai_local.LocalAIResponder()

        def bad(_text):
            raise QualityCheckFailed("hedge_phrase", "I don't know.")
            yield  # pragma: no cover

        called = []
        monkeypatch.setattr(r._hailo, "generate_stream", bad)
        monkeypatch.setattr(r._ollama, "generate_stream",
                            lambda t: called.append(1) or iter(["nope"]))

        with pytest.raises(QualityCheckFailed):
            list(r.generate_stream("hi"))
        assert called == [], "quality failures escalate to cloud, not Ollama"
