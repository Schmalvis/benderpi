"""What a child's words actually REACH in Halloween mode.

Written after a three-agent review found that the first version of this
feature gated the local model and left the entire handler chain in front of it
reachable. Every fault below was verified by running the real classifier, and
none of the 22 original tests could see any of them, because not one of them
called `intent.classify` or `Responder.get_response`.

These tests drive the real routing. They are the regression net for:
  - "tell me a joke" -> 1 of 8 household clips, one being
    "compare your lives to mine and then kill yourselves"
  - "happy halloween" -> a household line telling a child to keep a secret
  - "are you hungry"  -> "I run on alcohol. Beer mostly. Hand it over."
  - "can I be your friend" -> "You couldn't afford to be my friend."
  - "turn off the lights" -> actuates the owner's house
  - "what's happening" -> reads BBC news headlines to children
  - "what do you see" -> photographs them and calls the cloud with the
    HOUSEHOLD prompt

The household clips are all still present and unchanged. This mode must simply
never select them.
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.modules.setdefault("pyaudio", MagicMock())
sys.modules.setdefault("anthropic", MagicMock())

import intent as intent_mod
import prebuild_responses as pre
import responder as responder_mod
from config import cfg

# What children actually say at a door, and what it must NEVER reach.
FORBIDDEN_HANDLERS = {
    "RealClipHandler",    # household joke / dismissal / greeting clips
    "PreGenHandler",      # household PERSONAL bank
    "HAHandler",          # the owner's lights and heating
    "NewsHandler",        # BBC headlines
    "VisionHandler",      # camera + cloud with the household prompt
    "ContextualHandler",  # cloud + device telemetry + insults
    "TimerHandler",
    "WeatherHandler",
    "TimeHandler",
}


@pytest.fixture
def halloween(monkeypatch, tmp_path):
    """Halloween mode, with an index.json built from the real bank."""
    monkeypatch.setattr(cfg, "halloween_mode", True, raising=False)
    index = {
        "halloween": [
            {"pattern": e["pattern"],
             "file": f"speech/responses/halloween/{e['slug']}.wav",
             "label": e["text"]}
            for e in pre.HALLOWEEN_RESPONSES
        ],
        "promoted": [],
    }
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index))
    monkeypatch.setattr(intent_mod, "_INDEX_PATH", str(path))
    intent_mod.reload_promoted()
    yield
    intent_mod.reload_promoted()


@pytest.fixture
def household(monkeypatch):
    monkeypatch.setattr(cfg, "halloween_mode", False, raising=False)
    intent_mod.reload_promoted()
    yield
    intent_mod.reload_promoted()


class TestDispatchIsRestricted:
    def test_only_the_halloween_bank_handler_is_registered(self, halloween):
        r = responder_mod.Responder()
        names = {type(h).__name__ for hs in r._dispatch.values() for h in hs}
        assert names == {"PromotedHandler"}, names

    def test_none_of_the_dangerous_handlers_survive(self, halloween):
        r = responder_mod.Responder()
        names = {type(h).__name__ for hs in r._dispatch.values() for h in hs}
        assert not (names & FORBIDDEN_HANDLERS)

    def test_the_household_chain_is_untouched(self, household):
        """The household device must keep every feature it has."""
        r = responder_mod.Responder()
        names = {type(h).__name__ for hs in r._dispatch.values() for h in hs}
        assert FORBIDDEN_HANDLERS <= names

    def test_house_control_and_news_have_no_handler_in_halloween_mode(self, halloween):
        r = responder_mod.Responder()
        for dangerous in ("HA_CONTROL", "HA_STATUS", "NEWS", "VISION",
                          "WEATHER", "TIMER", "CONTEXTUAL"):
            assert not r._dispatch.get(dangerous), dangerous


class TestClassifierRoutes:
    """The bank is checked BEFORE every household intent. Order is the point:
    "happy halloween" matches PERSONAL/feelings otherwise."""

    @pytest.mark.parametrize("said,slug", [
        ("trick or treat", "trick_or_treat"),
        ("Trick or treat!", "trick_or_treat"),
        ("tell me a joke", "joke_skeleton"),
        ("got any jokes", "joke_skeleton"),
        ("happy halloween", "happy_halloween"),
        ("are you hungry", "food_offer"),
        ("do you want a drink", "food_offer"),
        ("nice costume", "nice_costume"),
        ("are you real", "are_you_real"),
        ("what are you", "what_are_you"),
        ("what's your name", "what_are_you"),
        ("can I have some candy", "candy_request"),
        ("thank you", "thank_you_kid"),
        ("bye", "goodbye_kid"),
        ("are you scary", "scary_reassure"),
        ("i'm scared", "scary_reassure"),
    ])
    def test_doorstep_lines_hit_the_bank(self, halloween, said, slug):
        name, sub = intent_mod.classify(said)
        assert name == "PROMOTED", f"{said!r} -> {name}"
        assert sub.endswith(f"{slug}.wav"), f"{said!r} -> {sub}"

    def test_the_joke_request_never_reaches_the_household_clips(self, halloween):
        """One of the 8 household joke clips says "compare your lives to mine
        and then kill yourselves". Verified present in speech/responses/
        index.json on 2026-10-01."""
        name, sub = intent_mod.classify("tell me a joke")
        assert name != "JOKE"
        assert "halloween" in sub

    def test_household_mode_still_reaches_its_own_clips(self, household):
        """The owner keeps the joke, the dismissals and the PERSONAL bank."""
        assert intent_mod.classify("tell me a joke")[0] == "JOKE"
        assert intent_mod.classify("happy halloween")[0] == "PERSONAL"
        assert intent_mod.classify("turn off the lights")[0] == "HA_CONTROL"


class TestUnmatchedSpeechGoesToTheGatedModel:
    def test_an_odd_question_has_no_handler_and_falls_through(self, halloween):
        r = responder_mod.Responder()
        name, _ = intent_mod.classify("why is the moon following us")
        assert not r._dispatch.get(name), \
            "unmatched speech must reach the gated model, not a household clip"

    def test_house_control_words_fall_through_rather_than_acting(self, halloween):
        """The intent still classifies as HA_CONTROL -- what matters is that
        nothing is registered to act on it."""
        r = responder_mod.Responder()
        name, _ = intent_mod.classify("turn off the lights")
        assert name == "HA_CONTROL"
        assert not r._dispatch.get(name)


class TestBankContent:
    def test_every_spoken_line_passes_a_read_aloud_check(self):
        """Said by a synthesiser to someone else's child, with nobody vetting
        it live. The list is deliberately broad."""
        banned = ("damn", "hell", "kill", "die", "died", "dead", "blood",
                  "stupid", "idiot", "shut up", "beer", "booze", "alcohol",
                  "drunk", "steal", "coffin", "corpse", "secret",
                  "don't tell", "come inside", "follow me", "scream",
                  "murder", "ghost will", "eat you")
        lines = ([e["text"] for e in pre.HALLOWEEN_RESPONSES]
                 + list(pre.HALLOWEEN_FALLBACKS)
                 + list(pre.HALLOWEEN_GREETINGS))
        for text in lines:
            low = text.lower()
            for word in banned:
                assert word not in low, f"{word!r} in {text!r}"

    def test_lines_are_short_enough_for_a_doorstep(self):
        for text in [e["text"] for e in pre.HALLOWEEN_RESPONSES]:
            assert len(text) < 180, text

    def test_a_greeting_bank_exists_because_the_greeting_bypasses_handlers(self):
        assert len(pre.HALLOWEEN_GREETINGS) >= 3

    def test_the_index_carries_all_three_sets(self):
        import inspect
        src = inspect.getsource(pre.build_index)
        for key in ('"halloween"', '"halloween_greeting"', '"halloween_fallback"'):
            assert key in src, key
