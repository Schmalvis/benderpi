"""Halloween autonomous mode: open mic, kid-safe prompt, nothing ungated spoken.

Context: Bender sits outside on Halloween with candy in his chest and talks to
other people's children, with the owner in the house but not beside him. A
trick-or-treater will never say "hey bender", so this mode drops the wake word
and treats any speech as the trigger. The owner's stated worst acceptable
failure is silence or "I have no idea what you're talking about", which is
what lets the gate be strict.

Plan: docs/superpowers/plans/2026-10-01-halloween-autonomous-bender.md
"""
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.modules.setdefault("anthropic", MagicMock())

import ai_local
import ai_response
import config as config_mod
import prebuild_responses as pre
from config import cfg


@pytest.fixture
def halloween(monkeypatch):
    monkeypatch.setattr(cfg, "halloween_mode", True, raising=False)


@pytest.fixture
def household(monkeypatch):
    monkeypatch.setattr(cfg, "halloween_mode", False, raising=False)


class TestStrictFlag:
    """This flag drops the wake word, swaps the prompt, forces local-only and
    blocks ungated replies. It must never switch itself on by accident: a test
    with a MagicMock cfg did exactly that, because any attribute of a Mock is
    truthy."""

    def test_only_literal_true_enables_it(self, monkeypatch):
        for value in (True,):
            monkeypatch.setattr(cfg, "halloween_mode", value, raising=False)
            assert config_mod.halloween_enabled() is True
        for value in (False, None, 0, 1, "true", "yes", MagicMock()):
            monkeypatch.setattr(cfg, "halloween_mode", value, raising=False)
            assert config_mod.halloween_enabled() is False, repr(value)

    def test_absent_flag_is_off(self, monkeypatch):
        monkeypatch.delattr(cfg, "halloween_mode", raising=False)
        assert config_mod.halloween_enabled() is False

    def test_it_ships_off(self):
        import json
        with open(os.path.join(os.path.dirname(__file__), "..",
                               "bender_config.json")) as f:
            assert json.load(f)["halloween_mode"] is False


class TestPromptSwitch:
    def test_halloween_mode_uses_the_kid_safe_prompt(self, halloween):
        assert ai_local.active_system_prompt() is ai_response.HALLOWEEN_SYSTEM_PROMPT

    def test_household_mode_is_unchanged(self, household):
        assert ai_local.active_system_prompt() is ai_response.BENDER_SYSTEM_PROMPT

    def test_the_kid_prompt_forbids_what_the_household_one_allows(self):
        kid = ai_response.HALLOWEEN_SYSTEM_PROMPT.lower()
        house = ai_response.BENDER_SYSTEM_PROMPT.lower()
        assert "mild profanity" in house          # fine for the family
        assert "never swear" in kid               # not for a stranger's child
        for forbidden in ("insult", "frightening", "personal information"):
            assert forbidden in kid, forbidden

    def test_the_kid_prompt_caps_length(self):
        assert "one or two short sentences" in \
            ai_response.HALLOWEEN_SYSTEM_PROMPT.lower()


class TestCaps:
    def test_tokens_and_sentences_are_tighter(self, halloween, monkeypatch):
        monkeypatch.setattr(cfg, "halloween_max_tokens", 48, raising=False)
        monkeypatch.setattr(cfg, "halloween_max_sentences", 2, raising=False)
        monkeypatch.setattr(cfg, "ai_hailo_max_tokens", 80, raising=False)
        monkeypatch.setattr(cfg, "ai_max_sentences", 3, raising=False)
        assert ai_local.active_max_tokens() == 48
        assert ai_local.active_max_sentences() == 2

    def test_household_caps_are_untouched(self, household, monkeypatch):
        monkeypatch.setattr(cfg, "ai_hailo_max_tokens", 80, raising=False)
        monkeypatch.setattr(cfg, "ai_max_sentences", 3, raising=False)
        assert ai_local.active_max_tokens() == 80
        assert ai_local.active_max_sentences() == 3

    def test_the_sampling_kwargs_actually_carry_the_cap(self, halloween, monkeypatch):
        monkeypatch.setattr(cfg, "halloween_max_tokens", 48, raising=False)
        assert ai_local._hailo_sampling_kwargs()["max_generated_tokens"] == 48


class TestResponseBank:
    def test_the_things_children_say_are_all_covered(self):
        import re
        patterns = [(e["slug"], re.compile(e["pattern"], re.I))
                    for e in pre.HALLOWEEN_RESPONSES]

        def matched(text):
            return [slug for slug, rx in patterns if rx.search(text)]

        for said in ("trick or treat", "Trick or treat!", "can I have some candy",
                     "any sweets?", "nice costume", "are you real",
                     "what are you", "happy halloween", "thank you"):
            assert matched(said), f"nothing answers {said!r}"

    def test_every_entry_has_a_usable_slug_and_text(self):
        for e in pre.HALLOWEEN_RESPONSES:
            assert e["slug"].replace("_", "").isalnum()
            assert len(e["text"]) > 10
            assert e["text"].count(".") + e["text"].count("!") <= 3

    def test_no_greeting_or_fallback_says_anything_unsuitable(self):
        """Read by a synthesiser to a child, with nobody vetting it live."""
        banned = ("damn", "hell", "kill", "die", "dead", "blood", "stupid",
                  "idiot", "shut up", "beer", "booze", "steal")
        for text in ([e["text"] for e in pre.HALLOWEEN_RESPONSES]
                     + pre.HALLOWEEN_FALLBACKS):
            low = text.lower()
            for word in banned:
                assert word not in low, f"{word!r} in {text!r}"

    def test_the_fallbacks_match_the_accepted_failure(self):
        joined = " ".join(pre.HALLOWEEN_FALLBACKS).lower()
        assert "no idea what you're talking about" in joined

    def test_the_index_exposes_both_sets(self):
        import inspect
        src = inspect.getsource(pre.build_index)
        assert '"halloween"' in src and '"halloween_fallback"' in src


# TestLoopWiring removed 2026-10-01. Its assertions read the source text --
# `"except RuntimeError" in body`, `src.index("return") < src.index(...)` --
# and a review showed they passed while an ordinary ValueError killed the
# process and leaked the session. Replaced by tests/test_halloween_loop.py,
# which executes the loops against fakes.


class TestGateBlocksUngatedSpeech:
    def test_a_failed_gate_returns_the_canned_line_not_the_text(self, halloween,
                                                                monkeypatch):
        import responder as responder_mod
        r = object.__new__(responder_mod.Responder)
        monkeypatch.setattr(responder_mod.metrics, "count", lambda *a, **k: None)
        monkeypatch.setattr(responder_mod.tts_generate, "speak",
                            lambda text: "/tmp/fake.wav")
        monkeypatch.setattr(responder_mod.glob, "glob", lambda pattern: [])
        resp = responder_mod.Responder._halloween_fallback(
            r, "what is your favourite swear word", "UNKNOWN", None, {})
        assert resp.method == "halloween_fallback"
        assert resp.text in responder_mod.Responder.HALLOWEEN_FALLBACKS

    def test_it_prefers_a_prebuilt_clip_over_live_tts(self, halloween, monkeypatch):
        """Live TTS costs ~1s at exactly the moment the turn already failed."""
        import responder as responder_mod
        r = object.__new__(responder_mod.Responder)
        monkeypatch.setattr(responder_mod.glob, "glob",
                            lambda pattern: ["/x/halloween/fallback_001.wav"])
        called = []
        monkeypatch.setattr(responder_mod.tts_generate, "speak",
                            lambda t: called.append(t) or "/tmp/x.wav")
        resp = responder_mod.Responder._halloween_fallback(
            r, "anything", "UNKNOWN", None, {})
        assert resp.wav_path == "/x/halloween/fallback_001.wav"
        assert called == []
        assert resp.is_temp is False
