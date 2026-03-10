#!/usr/bin/env python3
"""
leds.py — WS2812B LED strip control
12 LEDs on SPI MOSI (GPIO 10, Pin 19)
"""

import board
import busio
import neopixel_spi

NUM_LEDS   = 12
BRIGHTNESS = 0.8

# Colour used for VU meter (R, G, B) — warm amber
COLOUR = (255, 120, 0)

_spi    = busio.SPI(board.SCK, MOSI=board.MOSI)
pixels  = neopixel_spi.NeoPixel_SPI(_spi, NUM_LEDS, brightness=BRIGHTNESS, auto_write=False)


def all_off():
    pixels.fill((0, 0, 0))
    pixels.show()


def all_on(colour=COLOUR):
    pixels.fill(colour)
    pixels.show()


def set_level(level):
    """Light up `level` LEDs (0–NUM_LEDS) as a VU meter."""
    level = max(0, min(level, NUM_LEDS))
    for i in range(NUM_LEDS):
        if i < level:
            # Gradient: amber at low end, bright white at peak
            ratio = i / (NUM_LEDS - 1)
            r = 255
            g = int(120 + 135 * ratio)
            b = int(200 * ratio)
            pixels[i] = (r, g, b)
        else:
            pixels[i] = (0, 0, 0)
    pixels.show()
