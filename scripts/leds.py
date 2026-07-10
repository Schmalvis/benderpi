#!/usr/bin/env python3
"""
leds.py — WS2812B LED strip control
LEDs on SPI MOSI (GPIO 10, Pin 19)

Supports three modes:
  - Talking: amplitude-reactive (colour from cfg.led_talking_colour or cfg.led_colour)
  - Listening: solid colour (cfg.led_listening_colour, default blue)
  - Off: all LEDs off
"""

import threading
import time

import board
import busio
import neopixel_spi
from config import cfg
from logger import get_logger

log = get_logger("leds")

NUM_LEDS   = cfg.led_count
BRIGHTNESS = cfg.led_brightness

_spi    = busio.SPI(board.SCK, MOSI=board.MOSI)
pixels  = neopixel_spi.NeoPixel_SPI(_spi, NUM_LEDS, brightness=BRIGHTNESS, auto_write=False)

# Current mode — used by audio.py to pick the right colour
_mode = "off"  # "off", "listening", "talking"

# Alert flash state
#
# set_alert_flash() used to gate thread creation on a lone `_alert_active`
# bool plus a `not _alert_thread.is_alive()` check with no locking. Under
# rapid on/off/on toggling (e.g. two timer alerts firing back-to-back, or a
# double-click in the web UI) that check-then-start was not atomic: two
# calls could both observe the previous thread as dead/exiting and each
# spawn a fresh `_alert_loop` thread, leaving two loops fighting over the
# same NeoPixel/SPI bus. A `threading.Lock` around start/stop plus a
# `threading.Event` (instead of a plain bool the loop polls once per
# sleep(0.2)) makes stop-and-join deterministic: off() blocks until the
# previous loop has actually exited and blanked the strip before on() is
# allowed to start a new one.
_alert_thread = None
_alert_lock = threading.Lock()
_alert_stop_event = threading.Event()


def all_off():
    global _mode
    _mode = "off"
    pixels.fill((0, 0, 0))
    pixels.show()


def all_on(colour=None):
    if colour is None:
        colour = cfg.led_colour
    pixels.fill(colour)
    pixels.show()


def set_listening(on: bool = True):
    """Set LEDs to solid listening colour (blue by default). Call when Bender is listening."""
    global _mode
    if on and cfg.led_listening_enabled:
        _mode = "listening"
        pixels.fill(cfg.led_listening_colour)
        pixels.show()
    elif not on:
        all_off()


def set_talking():
    """Switch LEDs to talking mode. Actual amplitude updates via set_level()."""
    global _mode
    _mode = "talking"


def set_level(ratio, colour=None):
    """Flash all LEDs at brightness proportional to amplitude (0.0-1.0).
    Uses talking colour when in talking mode, or the provided colour override.
    """
    ratio = max(0.0, min(ratio, 1.0))
    if colour is None:
        if cfg.led_listening_enabled:
            colour = cfg.led_talking_colour
        else:
            colour = cfg.led_colour
    r = int(colour[0] * ratio)
    g = int(colour[1] * ratio)
    b = int(colour[2] * ratio)
    pixels.fill((r, g, b))
    pixels.show()


def set_alert_flash(on: bool):
    """Start/stop fast red-orange alternating flash for timer alerts.

    Guarded by _alert_lock so concurrent/rapid toggles can't race: off()
    signals the stop event and joins the running thread (bounded wait)
    before returning, so a subsequent on() never overlaps with a still-
    exiting previous loop.
    """
    global _alert_thread
    with _alert_lock:
        if on:
            if _alert_thread is not None and _alert_thread.is_alive():
                return  # already flashing — idempotent
            _alert_stop_event.clear()
            _alert_thread = threading.Thread(target=_alert_loop, daemon=True)
            _alert_thread.start()
        else:
            _alert_stop_event.set()
            if _alert_thread is not None:
                _alert_thread.join(timeout=1.0)
                if _alert_thread.is_alive():
                    log.warning("set_alert_flash: _alert_loop did not exit within 1s")
                _alert_thread = None


def _alert_loop():
    """Background thread that alternates red/orange on LEDs."""
    colours = [(255, 40, 0), (255, 140, 0)]  # red, orange
    idx = 0
    while not _alert_stop_event.is_set():
        pixels.fill(colours[idx % 2])
        pixels.show()
        idx += 1
        _alert_stop_event.wait(0.2)
    # Turn off when done
    pixels.fill((0, 0, 0))
    pixels.show()
