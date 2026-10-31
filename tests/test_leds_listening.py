"""Bender's mouth LED is white while he waits and listens, not blue.

Blue came only from cfg.led_listening_colour, through leds.set_listening(True).
It stays lit through capture and thinking, until reply audio drives the LEDs.
"""
import json
import os
import sys
import types


sys.path.insert(0, "scripts")

# leds.py imports hardware SPI libs at module level. Stub them off-device.
sys.modules.setdefault("board", types.SimpleNamespace(SCK=object(), MOSI=object()))
sys.modules.setdefault("busio", types.SimpleNamespace(SPI=lambda *a, **k: types.SimpleNamespace()))


class _FakeNeoPixel:
    def __init__(self, *a, **k):
        self.calls = []

    def fill(self, colour):
        self.calls.append(("fill", colour))

    def show(self):
        self.calls.append(("show", None))


sys.modules.setdefault("neopixel_spi", types.SimpleNamespace(NeoPixel_SPI=_FakeNeoPixel))

import leds  # noqa: E402
from config import Config  # noqa: E402

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SHIPPED = os.path.join(_BASE_DIR, "bender_config.json")
WHITE = (255, 255, 255)


def test_code_default_is_white(tmp_path):
    # A config path that does not exist leaves every code default in place.
    assert Config(config_path=str(tmp_path / "none.json")).led_listening_colour == WHITE


def test_shipped_config_value_is_white():
    with open(_SHIPPED) as f:
        assert json.load(f)["led_listening_colour"] == [255, 255, 255]


def test_shipped_config_loads_as_a_white_tuple():
    assert Config(config_path=_SHIPPED).led_listening_colour == WHITE


def test_set_listening_fills_with_the_loaded_shipped_colour(monkeypatch):
    shipped = Config(config_path=_SHIPPED)
    assert shipped.led_listening_enabled is True
    monkeypatch.setattr(leds, "cfg", shipped)
    fake = _FakeNeoPixel()
    monkeypatch.setattr(leds, "pixels", fake)
    leds.set_listening(True)
    assert fake.calls == [("fill", WHITE), ("show", None)]


def test_set_listening_fills_with_whatever_cfg_holds(monkeypatch):
    cfg = types.SimpleNamespace(led_listening_enabled=True, led_listening_colour=(1, 2, 3))
    monkeypatch.setattr(leds, "cfg", cfg)
    fake = _FakeNeoPixel()
    monkeypatch.setattr(leds, "pixels", fake)
    leds.set_listening(True)
    assert fake.calls[0] == ("fill", (1, 2, 3))


def test_set_listening_stays_dark_when_disabled(monkeypatch):
    cfg = types.SimpleNamespace(led_listening_enabled=False, led_listening_colour=WHITE)
    monkeypatch.setattr(leds, "cfg", cfg)
    fake = _FakeNeoPixel()
    monkeypatch.setattr(leds, "pixels", fake)
    monkeypatch.setattr(leds, "all_off", lambda: fake.calls.append(("off", None)))
    leds.set_listening(True)
    assert fake.calls == []
