#!/usr/bin/env python3
"""
leds.py — WS2812B LED strip control
12 LEDs on SPI MOSI (GPIO 10, Pin 19)
"""

import board
import busio
import neopixel_spi
from logger import get_logger

log = get_logger("leds")

NUM_LEDS   = 12
BRIGHTNESS = 0.8

# Colour (R, G, B) — warm amber
COLOUR = (255, 120, 0)

_spi    = busio.SPI(board.SCK, MOSI=board.MOSI)
pixels  = neopixel_spi.NeoPixel_SPI(_spi, NUM_LEDS, brightness=BRIGHTNESS, auto_write=False)


def all_off():
    pixels.fill((0, 0, 0))
    pixels.show()


def all_on(colour=COLOUR):
    pixels.fill(colour)
    pixels.show()


def set_level(ratio):
    """Flash all LEDs at brightness proportional to amplitude (0.0–1.0)."""
    ratio = max(0.0, min(ratio, 1.0))
    r = int(COLOUR[0] * ratio)
    g = int(COLOUR[1] * ratio)
    b = int(COLOUR[2] * ratio)
    pixels.fill((r, g, b))
    pixels.show()
