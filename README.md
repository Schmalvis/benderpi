# BenderPi — Bender Voice Assistant Project

## Hardware
- **Device:** Raspberry Pi 5 (BenderPi, 192.168.68.132)
- **Audio HAT:** Adafruit Voice Bonnet (WM8960 codec)
  - I2S audio in/out
  - 2x onboard MEMS microphones
  - 2x 1W speaker outputs (mono in use)
  - Registered as ALSA card 2 (`seeed-2mic-voicecard`)
- **Speaker:** 3W passive speaker connected to Voice Bonnet output

## Audio Setup
- Driver: `seeed-voicecard` (HinTak fork, v6.12 branch) installed via DKMS
- ALSA default: card 2 (WM8960) — configured in `/etc/asound.conf`
- Playback and capture both on card 2, device 0
- Volume persisted via `alsactl store` — stored in `/var/lib/alsa/asound.state`

## Project Structure
```
~/bender/
├── README.md          — this file
├── .env               — local credentials (not committed)
├── speech/
│   ├── metadata.csv   — pipe-separated: wav/filename.wav | transcript
│   └── wav/           — Bender (Futurama) speech clips (.wav, 44100Hz mono)
├── scripts/
│   ├── wake.py        — wake word detection + greeting playback
│   └── hey-bender.ppn — Porcupine wake word model (Raspberry Pi / arm64)
└── logs/              — runtime logs
```

## Wake Word
Wake word detection uses **[Porcupine](https://picovoice.ai/platform/porcupine/)** (Picovoice). On detecting "Hey Bender", a random greeting clip is played through the speaker.

### Setup
1. Obtain a free access key from [console.picovoice.ai](https://console.picovoice.ai)
2. Train a custom "Hey Bender" wake word and download the `.ppn` for Raspberry Pi (arm64)
3. Place the `.ppn` in `scripts/`
4. Create `.env` in the project root:
   ```
   PORCUPINE_ACCESS_KEY=your_key_here
   ```

### Running
```bash
cd ~/bender/scripts
python3 wake.py
```

Say **"Hey Bender"** — the terminal will confirm detection and a greeting will play.

### Persisting Volume
ALSA mixer state is saved across reboots via:
```bash
sudo alsactl store
```

## History
- Originally fitted with Pimoroni Audio AMP SHIM (MAX98357A) — replaced with Adafruit Voice Bonnet
- Voice Bonnet adds microphone input enabling wake word detection and future STT capability

## Future / Roadmap

### Migrate wake word to OpenWakeWord
Replace Porcupine (requires API key) with **[OpenWakeWord](https://github.com/dscripka/openWakeWord)** for a fully local, no-account solution.

Key notes:
- The existing `hey-bender.ppn` cannot be reused — OpenWakeWord uses ONNX format
- A new model must be trained using OpenWakeWords automated pipeline, which synthesises "hey bender" audio via TTS and trains a small neural net
- Training should be done off-device (Google Colab recommended, ~30–60 mins)
- Code change is low effort — detection loop structure is near-identical to current Porcupine implementation
