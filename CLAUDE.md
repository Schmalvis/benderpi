# BenderPi — Claude Code Context

This file provides context for Claude Code sessions working on the BenderPi project.

---

## What This Project Is

A Raspberry Pi 5 based voice assistant using the character Bender from Futurama as its personality. On hearing the wake word **"Hey Bender"**, it plays a random Bender speech clip through a speaker with LED visualisation.

---

## Device

- **Name:** BenderPi (`192.168.68.132`, hostname: `BenderPi`)
- **User:** `pi`
- **OS:** Raspberry Pi OS (Debian trixie, aarch64)
- **SSH:** `ssh pi@192.168.68.132` — key at `~/.ssh/id_ed25519`
- **Working directory:** `/home/pi/bender`

---

## Hardware

| Component | Detail |
|---|---|
| Board | Raspberry Pi 5 |
| Audio HAT | Adafruit Voice Bonnet (WM8960 codec) |
| Speaker | 3W passive, connected to Voice Bonnet |
| LEDs | 12x WS2812B addressable RGB (5V DC) |
| LED Power | Pin 2 (5V) |
| LED Ground | Pin 6 |
| LED Data | GPIO 10 / SPI MOSI (Pin 19) — moved from GPIO 23 for Pi 5 SPI compatibility |

---

## Audio

- Driver: `seeed-voicecard` DKMS (HinTak fork, v6.12 branch)
- WM8960 registered as ALSA card 2 (`seeed-2mic-voicecard`)
- Default card set in `/etc/asound.conf`
- Playback via `pyaudio` (not `aplay`) to enable real-time amplitude sampling
- **Volume:** 85% — to persist after reboot:
  ```bash
  amixer -c 2 sset Speaker 85%
  sudo alsactl store
  sudo cp /var/lib/alsa/asound.state /etc/voicecard/wm8960_asound.state
  ```
  Both files must be updated — the seeed-voicecard service restores its own state on boot.

---

## Wake Word

- Library: **Porcupine** (Picovoice) — `pvporcupine`, `pvrecorder`
- Model: `scripts/hey-bender.ppn` (trained at console.picovoice.ai, Raspberry Pi / arm64 target)
- Access key stored in `.env` (not committed):
  ```
  PORCUPINE_ACCESS_KEY=your_key_here
  ```
- On detection: plays a random clip from `speech/greetings.txt`

---

## LEDs

- Library: `adafruit-circuitpython-neopixel-spi` + `adafruit-blinka`
- SPI enabled via `dtparam=spi=on` in `/boot/firmware/config.txt`
- During playback: all 12 LEDs flash together, brightness proportional to audio amplitude (RMS)
- Colour: warm amber `(255, 120, 0)`

---

## Service

The wake word detector runs as a systemd service:

```bash
sudo systemctl start bender-wakeword
sudo systemctl stop bender-wakeword
sudo systemctl restart bender-wakeword
journalctl -u bender-wakeword -f
```

Service file: `/etc/systemd/system/bender-wakeword.service`
Enabled on boot: yes

---

## Scripts

| File | Purpose |
|---|---|
| `scripts/wake.py` | Main entry point — wake word loop, loads greetings, drives playback |
| `scripts/audio.py` | WAV playback via pyaudio with real-time LED amplitude sync |
| `scripts/leds.py` | WS2812B LED control module (`set_level`, `all_on`, `all_off`) |
| `scripts/leds_test.py` | Standalone LED test (on, off, flash x3) |

---

## Speech Clips

- 88 x `.wav` files in `speech/wav/` (local copy on BenderPi)
- Source on network share: `/home/pi/share/bender-sounds/wav/` (CIFS mount from RPi5)
- Metadata: `speech/metadata.csv` — pipe-separated, `wav/filename.wav | transcript`
- Greetings (played on wake): `speech/greetings.txt` — one filename per line, `#` for comments

---

## Completed Work

- [x] Network share (`rpi-share`) mounted at `/home/pi/share` via fstab (CIFS, RPi5)
- [x] Adafruit Voice Bonnet (WM8960) installed and configured (replaced Pimoroni Audio AMP SHIM)
- [x] 88 Bender speech clips copied locally to `speech/wav/`
- [x] Wake word detection with Porcupine ("Hey Bender")
- [x] Greeting playback on wake word detection
- [x] Greetings list externalised to `speech/greetings.txt`
- [x] WS2812B LEDs wired and working via SPI
- [x] Audio-reactive LEDs — all flash in sync with amplitude during playback
- [x] `bender-wakeword` systemd service running and enabled on boot
- [x] Volume persisted at 85% across reboots

## Roadmap

- [ ] Migrate wake word to **OpenWakeWord** (fully local, no API key)
  - Requires training a custom ONNX model (Google Colab recommended)
  - Code change is low effort once model exists
- [ ] STT (speech-to-text) — process commands after wake word
- [ ] TTS — generate Bender-style responses beyond pre-recorded clips
- [ ] Idle LED animation (e.g. slow pulse when listening)
