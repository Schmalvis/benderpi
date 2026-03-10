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
- I2C address: 0x1A

## Project Structure
```
~/bender/
├── README.md          — this file
├── speech/
│   ├── metadata.csv   — pipe-separated: wav/filename.wav | transcript
│   └── wav/           — 88 Bender (Futurama) speech clips (.wav, 44100Hz mono)
├── scripts/           — automation / playback scripts
└── logs/              — runtime logs
```

## Speech Clips
- Source: rpi-share (`/home/pi/share/bender-sounds/wav/`)
- 88 .wav clips, Bender (Futurama) character voice
- Metadata: `speech/metadata.csv` — pipe-separated, col1=relative path, col2=transcript

## Network Share
- rpi-share mounted at `/home/pi/share` (CIFS from RPi5, `//192.168.68.139/rpi5-share`)
- fstab entry: `x-systemd.automount`

## History
- Originally fitted with Pimoroni Audio AMP SHIM (MAX98357A) — replaced with Voice Bonnet
- Voice Bonnet adds microphone input for future STT/wake-word capability
