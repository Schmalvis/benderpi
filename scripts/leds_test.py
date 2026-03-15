#!/usr/bin/env python3
"""
leds_test.py — Basic WS2812B LED on/off test
12 LEDs on SPI MOSI (GPIO 10, Pin 19)

Usage:
    sudo python3 leds_test.py
"""

import time
import board
import busio
import neopixel_spi

NUM_LEDS  = 45
BRIGHTNESS = 0.3  # 0.0 – 1.0

spi    = busio.SPI(board.SCK, MOSI=board.MOSI)
pixels = neopixel_spi.NeoPixel_SPI(spi, NUM_LEDS, brightness=BRIGHTNESS, auto_write=False)

def all_on(colour=(255, 255, 255)):
    pixels.fill(colour)
    pixels.show()

def all_off():
    pixels.fill((0, 0, 0))
    pixels.show()

if __name__ == "__main__":
    print("LEDs ON")
    all_on()
    time.sleep(3)

    print("LEDs OFF")
    all_off()
    time.sleep(1)

    print("Flashing 3 times...")
    for _ in range(3):
        all_on((0, 150, 255))  # blue-white
        time.sleep(0.5)
        all_off()
        time.sleep(0.5)

    print("Done.")
