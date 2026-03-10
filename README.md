# BenderPi — Bender Voice Assistant Project

## Hardware
- **Device:** Raspberry Pi 5 (BenderPi, 192.168.68.132)
- **Audio HAT:** Adafruit Voice Bonnet (WM8960 codec)
  - I2S audio in/out
  - 2x onboard MEMS microphones
  - 2x 1W speaker outputs (mono in use)
  - Registered as ALSA card 2 (`seeed-2mic-voicecard`)
- **Speaker:** 3W passive speaker connected to Voice Bonnet output
- **LEDs:** 12x WS2812B addressable RGB LEDs (5V, DC)
  - Power: Pin 2 (5V), Ground: Pin 6, Data: GPIO 10 / SPI MOSI (Pin 19)

## Audio Setup
- Driver: `seeed-voicecard` (HinTak fork, v6.12 branch) installed via DKMS
- ALSA default: card 2 (WM8960) — configured in `/etc/asound.conf`
- Playback and capture both on card 2, device 0
- Volume persisted via `alsactl store` — state written to both `/var/lib/alsa/asound.state` and `/etc/voicecard/wm8960_asound.state` to survive reboots

## Project Structure
```
~/bender/
├── README.md              — this file
├── .env                   — local credentials (not committed)
├── requirements.txt       — Python dependencies
├── speech/
│   ├── metadata.csv       — pipe-separated: wav/filename.wav | transcript
│   ├── greetings.txt      — clips played at random on wake word detection
│   └── wav/               — Bender (Futurama) speech clips (.wav, 44100Hz mono)
└── scripts/
    ├── wake.py            — wake word detection + greeting playback
    ├── audio.py           — WAV playback with real-time LED sync
    ├── leds.py            — WS2812B LED control
    ├── leds_test.py       — standalone LED test script
    └── hey-bender.ppn     — Porcupine wake word model (Raspberry Pi / arm64)
```

## Wake Word
Wake word detection uses **[Porcupine](https://picovoice.ai/platform/porcupine/)** (Picovoice). On detecting "Hey Bender", a random clip from `speech/greetings.txt` is played through the speaker with LEDs reacting in real time.

### Setup
1. Obtain a free access key from [console.picovoice.ai](https://console.picovoice.ai)
2. Train a custom "Hey Bender" wake word and download the `.ppn` for Raspberry Pi (arm64)
3. Place the `.ppn` in `scripts/`
4. Create `.env` in the project root:
   ```
   PORCUPINE_ACCESS_KEY=your_key_here
   ```

### Running manually
```bash
cd ~/bender/scripts
python3 wake.py
```

### Running as a service
The wake word detector runs continuously as a systemd service:
```bash
sudo systemctl start bender-wakeword    # start
sudo systemctl stop bender-wakeword     # stop
sudo systemctl restart bender-wakeword  # restart
journalctl -u bender-wakeword -f        # logs
```
The service is enabled on boot automatically.

### Persisting Volume
ALSA mixer state must be written to two locations to survive reboots:
```bash
amixer -c 2 sset Speaker 85%
sudo alsactl store
sudo cp /var/lib/alsa/asound.state /etc/voicecard/wm8960_asound.state
```

## LEDs
12x WS2812B LEDs driven via SPI. During audio playback, all LEDs flash together with brightness proportional to the audio amplitude.

- `leds.py` — importable module, `set_level(ratio)`, `all_on()`, `all_off()`
- `leds_test.py` — standalone test: turns LEDs on, off, then flashes 3 times
- SPI must be enabled: `dtparam=spi=on` in `/boot/firmware/config.txt`

## History
- Originally fitted with Pimoroni Audio AMP SHIM (MAX98357A) — replaced with Adafruit Voice Bonnet
- Data line moved from GPIO 23 to GPIO 10 (SPI MOSI) for reliable Pi 5 compatibility
- Voice Bonnet adds microphone input enabling wake word detection and future STT capability

## Future / Roadmap

### Migrate wake word to OpenWakeWord
Replace Porcupine (requires API key) with **[OpenWakeWord](https://github.com/dscripka/openWakeWord)** for a fully local, no-account solution.

Key notes:
- The existing `hey-bender.ppn` cannot be reused — OpenWakeWord uses ONNX format
- A new model must be trained using OpenWakeWord's automated pipeline, which synthesises "hey bender" audio via TTS and trains a small neural net
- Training should be done off-device (Google Colab recommended, ~30–60 mins)
- Code change is low effort — detection loop structure is near-identical to current Porcupine implementation
