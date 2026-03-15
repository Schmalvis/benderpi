# BenderPi

A Raspberry Pi 5 voice assistant with the personality of Bender Bending Rodriguez from Futurama. Say "Hey Bender" — he wakes up, listens, and responds in his own voice using a fine-tuned TTS model and a hybrid offline/AI conversation system.

---

## Hardware

| Component | Detail |
|---|---|
| Device | Raspberry Pi 5 |
| Audio HAT | Seeed 2-Mic Pi HAT (WM8960 codec) — I2S, 2x MEMS mics, speaker output |
| Speaker | 3W passive |
| LEDs | 45× WS2812B addressable RGB, SPI MOSI (GPIO 10) |
| ALSA card | Card 2 (`seeed-2mic-voicecard`) |

---

## Prerequisites

Before running `setup.sh`, these must be in place on a fresh Pi OS (64-bit, aarch64):

### 1. seeed-voicecard driver

The WM8960 codec requires a kernel driver installed via DKMS:

```bash
git clone https://github.com/HinTak/seeed-voicecard
cd seeed-voicecard
sudo ./install.sh
sudo reboot
```

After reboot, verify with `aplay -l` — card 2 should be `seeed-2mic-voicecard`.

Persist volume after install:
```bash
amixer -c 2 sset Speaker 85%
sudo alsactl store
sudo cp /var/lib/alsa/asound.state /etc/voicecard/wm8960_asound.state
```

### 2. Enable SPI (for LEDs)

Add to `/boot/firmware/config.txt`:
```
dtparam=spi=on
```
Then reboot. Verify: `ls /dev/spidev*` should return `/dev/spidev0.0`.

### 3. API keys

You'll need the following before setup:

| Key | Where to get it |
|---|---|
| **Picovoice access key** | [console.picovoice.ai](https://console.picovoice.ai) — free tier available |
| **Anthropic API key** | [console.anthropic.com](https://console.anthropic.com) |
| **Home Assistant long-lived token** | HA → Profile → Long-Lived Access Tokens |
| **Hugging Face token** | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) — needed to download voice model and audio assets |

---

## Setup

```bash
git clone git@github.com:Schmalvis/benderpi.git
cd benderpi
chmod +x setup.sh
./setup.sh
```

`setup.sh` handles everything:
- System package installation
- Python venv creation (`--system-site-packages` for hardware libs)
- Piper TTS binary download
- Voice model + audio clip download from Hugging Face Hub
- `.env` configuration (interactive prompts)
- TTS response library pre-build
- systemd service installation and enablement
- sudoers configuration for web UI

Once complete, start the services:
```bash
sudo systemctl start bender-converse
sudo systemctl start bender-web
```

Web UI: `http://<pi-ip>:8080`

---

## Environment Variables

Stored in `.env` (created by `setup.sh` from `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `PORCUPINE_ACCESS_KEY` | Yes | Picovoice wake word key |
| `ANTHROPIC_API_KEY` | Yes | Claude API key for AI fallback |
| `HA_TOKEN` | Yes | Home Assistant long-lived access token |
| `HA_URL` | Yes | HA base URL (e.g. `http://homeassistant.local:8123`) |
| `HA_WEATHER_ENTITY` | Optional | HA weather entity (default: `weather.forecast_home`) |
| `BENDER_AI_MODEL` | Optional | Claude model ID (default: `claude-haiku-4-5-20251001`) |
| `BENDER_WEB_PIN` | Yes | Web UI access PIN |
| `BENDER_WEB_PORT` | Optional | Web UI port (default: `8080`) |

---

## Project Structure

```
benderpi/
├── setup.sh                      — one-shot installer
├── .env.example                  — environment variable template
├── requirements.txt              — Python dependencies
│
├── scripts/
│   ├── wake_converse.py          — main conversation loop (wake → STT → intent → response)
│   ├── stt.py                    — speech-to-text (faster-whisper + webrtcvad)
│   ├── intent.py                 — intent classifier (keyword/regex rules)
│   ├── tts_generate.py           — Piper TTS wrapper
│   ├── ai_response.py            — Claude API fallback with Bender persona
│   ├── briefings.py              — cached weather + news WAV generation
│   ├── audio.py                  — WAV playback with LED amplitude sync
│   ├── leds.py                   — WS2812B LED strip control
│   ├── config.py                 — config loader (bender_config.json)
│   ├── logger.py                 — structured JSON-Lines logger
│   ├── metrics.py                — performance metrics
│   ├── prebuild_responses.py     — generates static TTS response library
│   ├── review_log.py             — log analysis + AI promotion suggestions
│   ├── git_pull.sh               — auto-pull script (used by systemd timer)
│   ├── handlers/
│   │   ├── ha_control.py         — Home Assistant entity control
│   │   └── weather.py            — HA weather fetch → Bender response text
│   └── web/
│       ├── app.py                — FastAPI web UI
│       ├── auth.py               — PIN authentication
│       └── static/               — web UI frontend
│
├── systemd/
│   ├── bender-converse.service   — main conversation service
│   ├── bender-web.service        — web UI service
│   ├── bender-git-pull.service   — auto-pull from GitHub
│   └── bender-git-pull.timer     — fires every 5 minutes
│
├── speech/
│   ├── metadata.csv              — Bender clip transcripts (LJSpeech format)
│   ├── greetings.txt             — filenames for random greeting selection
│   ├── wav/                      — original Bender audio clips (gitignored)
│   └── responses/
│       ├── index.json            — intent → WAV path mapping
│       ├── thinking/             — played while AI generates response
│       ├── joke/                 — pre-generated TTS jokes
│       ├── personal/             — pre-generated personal Q&A
│       ├── ha_confirm/           — HA confirmation fallbacks
│       └── daily/                — cached weather + news briefings (gitignored)
│
├── models/                       — ONNX voice model (gitignored, ~61MB)
├── piper/                        — Piper inference binary (gitignored, ~51MB)
└── logs/                         — conversation logs (gitignored)
```

---

## How It Works

### Conversation flow

```
"Hey Bender" detected
        ↓
Play greeting clip (original Bender audio)
        ↓
Listen → VAD silence detection → faster-whisper transcription
        ↓
Intent classification (keyword/regex, ~0ms)
        ↓
Response priority:
  1. Original Bender clip       → speech/wav/
  2. Pre-generated TTS          → speech/responses/<category>/
  3. Dynamic handler            → briefings.py / ha_control.py
  4. AI fallback (Claude)       → tts_generate.py → Piper
        ↓
Play response + LED amplitude sync
        ↓
Loop (8s silence timeout ends session)
```

### Intents

| Intent | Example phrases | Response source |
|---|---|---|
| GREETING | "hello", "how are you" | Original clips |
| AFFIRMATION | "thanks", "nice one" | Original clips |
| DISMISSAL | "bye", "stop" | Original clips — ends session |
| JOKE | "tell me a joke" | Original clips + pre-gen TTS |
| PERSONAL | "how old are you", "what do you eat" | Pre-gen TTS |
| WEATHER | "what's the weather", "is it raining" | Cached HA briefing (30min TTL) |
| NEWS | "headlines", "what's happening" | Cached BBC RSS briefing (2h TTL) |
| HA_CONTROL | "lights on", "set temp to 20" | Live HA entity control |
| UNKNOWN | Everything else | Claude API → Piper TTS |

### Weather & News

Both are pre-generated as WAVs and cached — no live generation on each request.

| Briefing | Source | TTL |
|---|---|---|
| Weather | Home Assistant `HA_WEATHER_ENTITY` | 30 minutes |
| News | BBC RSS (UK + England) | 2 hours |

Refreshed at service start and lazily on TTL expiry. Force refresh: `sudo systemctl restart bender-converse`

---

## Web UI

Available at `http://<pi-ip>:8080` — PIN protected.

- **Dashboard** — service status, uptime, conversation metrics
- **Puppet mode** — type text for Bender to speak, or play any audio clip
- **Config** — speech rate, AI model, VAD sensitivity, silence timeout
- **Logs** — conversation history, system logs, metrics

Puppet speak/clip temporarily stops `bender-converse` to release the audio device, then restarts it.

---

## Service Management

```bash
# Services
sudo systemctl start|stop|restart bender-converse
sudo systemctl start|stop|restart bender-web
journalctl -u bender-converse -f    # live logs

# Rebuild TTS response library (after editing prebuild_responses.py)
venv/bin/python3 scripts/prebuild_responses.py

# Review conversation logs
venv/bin/python3 scripts/review_log.py
venv/bin/python3 scripts/review_log.py --days 30
```

---

## Adding Responses

Edit `scripts/prebuild_responses.py` and add to the relevant section:

```python
# New joke
JOKE_RESPONSES = [
    ...
    "Your new Bender line here.",
]

# New personal Q&A topic
PERSONAL_RESPONSES = {
    ...
    "new_topic": "Bender's answer here.",
}
```

Then rebuild:
```bash
venv/bin/python3 scripts/prebuild_responses.py
```

### Promoting frequent AI responses to static

When `review_log.py` flags a query with `*** PROMOTE?`, add it to `PROMOTED_RESPONSES` in `prebuild_responses.py`:

```python
PROMOTED_RESPONSES = [
    {
        "slug":    "meaning_of_life",
        "pattern": r"meaning of life",
        "text":    "Forty. No wait — it's bending. Everything is bending.",
    },
]
```

Rebuild, and it'll be served from cache instead of hitting the Claude API.

---

## Voice Model

The Bender voice is a Piper VITS model fine-tuned from `en_US-lessac-medium` on original Bender speech clips (cleaned, separated, resampled to 22050Hz). Training: 5000 epochs on a T4 GPU via Hugging Face Jobs.

To train your own:
- See `share/bender-piper-training.ipynb` (Google Colab notebook)
- Dataset format: LJSpeech (WAV + `metadata.csv`)

---

## Auto-Deploy

A systemd timer (`bender-git-pull.timer`) polls GitHub every 5 minutes. If the branch has advanced, it pulls and restarts `bender-converse` automatically. Push to `main` → live on the Pi within 5 minutes.

---

## Notes for Contributors

- **Audio device**: the WM8960 is a single-rate codec. `bender-converse` holds it at 16 kHz (porcupine mic). The web UI stops `bender-converse` before any audio playback and restarts it after. Keep this constraint in mind when modifying audio handling.
- **PyAudio**: a single shared `pyaudio.PyAudio()` instance is created at module load in `audio.py`. Do not create additional instances — concurrent init causes PortAudio assertion failures.
- **venv**: always use `--system-site-packages` — hardware libs (lgpio, adafruit-blinka) are installed system-wide and won't be found otherwise.
- **Port variables**: host ports in service files must come from environment variables, never hardcoded.
