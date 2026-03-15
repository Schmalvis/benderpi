"""Tests for HA control parse functions."""

def test_parse_action_on():
    from handlers.ha_control import _parse_action
    assert _parse_action("turn on the lights") == "on"
    assert _parse_action("switch off the kitchen") == "off"
    assert _parse_action("what time is it") is None

def test_parse_room_term():
    from handlers.ha_control import _parse_room_term
    assert "office" in _parse_room_term("turn on the lights in my office")
    assert "kitchen" in _parse_room_term("kitchen lights off")

def test_parse_temperature():
    from handlers.ha_control import _parse_temperature
    assert _parse_temperature("set it to 21 degrees") == 21.0
    assert _parse_temperature("hello") is None
