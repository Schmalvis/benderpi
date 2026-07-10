"""Tests for intent classifier — focus on false positive prevention."""
from intent import classify

# === True positives (should still match) ===

def test_greeting_hello():
    assert classify("hello")[0] == "GREETING"

def test_dismissal_bye():
    assert classify("bye")[0] == "DISMISSAL"

def test_affirmation_thanks():
    assert classify("thanks")[0] == "AFFIRMATION"

def test_joke_request():
    assert classify("tell me a joke")[0] == "JOKE"

def test_weather():
    assert classify("what's the weather like")[0] == "WEATHER"

def test_news():
    assert classify("what's the news")[0] == "NEWS"

def test_ha_lights_on():
    assert classify("turn on the kitchen lights")[0] == "HA_CONTROL"

def test_personal_age():
    intent, sub = classify("how old are you")
    assert intent == "PERSONAL"
    assert sub == "age"

# === False positives (should NOT match simple intents) ===

def test_good_restaurant_not_affirmation():
    intent, _ = classify("what is a good restaurant near here")
    assert intent != "AFFIRMATION"

def test_stop_in_sentence_not_dismissal():
    intent, _ = classify("please don't stop the music")
    assert intent != "DISMISSAL"

def test_ok_in_question_not_affirmation():
    intent, _ = classify("is it ok to eat cheese before bed")
    assert intent != "AFFIRMATION"

def test_home_lights_not_personal():
    intent, _ = classify("turn on the home lights")
    assert intent == "HA_CONTROL"

def test_good_morning_is_greeting():
    intent, _ = classify("good morning")
    assert intent == "GREETING"

def test_long_utterance_not_simple():
    intent, _ = classify("can you tell me what is a good way to learn python programming")
    assert intent == "UNKNOWN"

def test_unknown_fallthrough():
    assert classify("explain quantum entanglement to me")[0] == "UNKNOWN"


# === Timer intents ===

def test_set_timer():
    assert classify("set a timer for 10 minutes")[0] == "TIMER"

def test_set_timer_named():
    assert classify("set a timer for pasta for 10 minutes")[0] == "TIMER"

def test_set_alarm():
    assert classify("set an alarm for 10am")[0] == "TIMER"

def test_wake_me():
    assert classify("wake me up at 6am")[0] == "TIMER"

def test_remind_me():
    assert classify("remind me in 30 minutes")[0] == "TIMER"

def test_cancel_timer():
    assert classify("cancel the pasta timer")[0] == "TIMER_CANCEL"

def test_cancel_alarm():
    assert classify("cancel my alarm")[0] == "TIMER_CANCEL"

def test_timer_status():
    assert classify("how long left on the timer")[0] == "TIMER_STATUS"

def test_what_timers():
    assert classify("what timers do I have")[0] == "TIMER_STATUS"

def test_any_alarms():
    assert classify("any alarms set")[0] == "TIMER_STATUS"


class TestContextualIntent:
    def test_what_time(self):
        intent, sub = classify("what time is it")
        assert intent == "CONTEXTUAL"
        assert sub == "time"

    def test_whats_the_time(self):
        intent, sub = classify("what's the time")
        assert intent == "CONTEXTUAL"
        assert sub == "time"

    def test_what_date(self):
        intent, sub = classify("what's the date today")
        assert intent == "CONTEXTUAL"
        assert sub == "date"

    def test_what_day(self):
        intent, sub = classify("what day is it")
        assert intent == "CONTEXTUAL"
        assert sub == "date"

    def test_temperature(self):
        intent, sub = classify("how hot is it")
        assert intent == "CONTEXTUAL"
        assert sub == "weather_detail"

    def test_is_it_raining(self):
        intent, sub = classify("is it raining outside")
        assert intent == "CONTEXTUAL"
        assert sub == "weather_detail"

    def test_status_how_are_you_doing(self):
        intent, sub = classify("how are you doing")
        assert intent == "CONTEXTUAL"
        assert sub == "status"

    def test_system_status(self):
        intent, sub = classify("system status")
        assert intent == "CONTEXTUAL"
        assert sub == "status"

    def test_feelings_stays_personal(self):
        intent, sub = classify("how are you feeling")
        assert intent == "PERSONAL"
        assert sub == "feelings"

    def test_how_are_you_bare_stays_personal(self):
        intent, sub = classify("how are you")
        assert intent == "PERSONAL"


# === Vision intent ===

import pytest

@pytest.mark.parametrize("text,expected_intent", [
    ("what do you see", "VISION"),
    ("who's in the room", "VISION"),
    ("describe the room", "VISION"),
    ("look around", "VISION"),
    ("what is in front of you", "VISION"),
    ("tell me a joke", "JOKE"),  # ensure VISION does not over-match
])
def test_vision_intent_patterns(text, expected_intent):
    from intent import classify
    intent, _ = classify(text)
    assert intent == expected_intent


# === HA_CONTROL vs HA_STATUS vs DISMISSAL precedence ===
#
# HA_CONTROL issues real HA writes (toggles real lights/radiators), so a
# question or narration about device state must never be misread as a
# command, and a dismissal ("bender, stop") must always win over an
# HA-shaped pattern.

@pytest.mark.parametrize("text,expected_intent", [
    # Imperatives — real commands, must stay HA_CONTROL
    ("turn on the kitchen lights", "HA_CONTROL"),
    ("turn off the office light", "HA_CONTROL"),
    ("bedroom lights off", "HA_CONTROL"),
    ("switch on the conservatory lights", "HA_CONTROL"),
    ("set the office radiator to 20 degrees", "HA_CONTROL"),
    # Questions / narration — read-only, must never toggle a real device
    ("is the office light on", "HA_STATUS"),
    ("are the kitchen lights on", "HA_STATUS"),
    ("was the heating left on", "HA_STATUS"),
    ("did you turn on the office lights", "HA_STATUS"),
    ("the lights in the kitchen have been turned on", "HA_STATUS"),
    ("the radiator turned itself off", "HA_STATUS"),
    ("are any lights on", "HA_STATUS"),
    ("what's the office temperature", "HA_STATUS"),
])
def test_ha_control_vs_status(text, expected_intent):
    from intent import classify
    intent, _ = classify(text)
    assert intent == expected_intent


@pytest.mark.parametrize("text", [
    "bender, stop",
    "bender stop",
    "shut up bender",
])
def test_dismissal_wins_over_ha_control(text):
    """DISMISSAL is a session-control intent and must always win, even if the
    utterance happens to also look HA-shaped."""
    from intent import classify
    intent, _ = classify(text)
    assert intent == "DISMISSAL"


def test_timer_cancel_not_shadowed_by_dismissal_reorder():
    """Moving DISMISSAL above HA_CONTROL must not let its bare \\bstop\\b
    pattern swallow 'stop the timer' — TIMER_CANCEL must still win."""
    from intent import classify
    assert classify("stop the timer")[0] == "TIMER_CANCEL"
    assert classify("cancel the pasta timer")[0] == "TIMER_CANCEL"
