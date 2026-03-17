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
