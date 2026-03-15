# BenderPi — Bender Voice Assistant

A Raspberry Pi 5 voice assistant with the personality of Bender Bending Rodriguez from Futurama. Responds to "Hey Bender", speaks in Bender's voice using a fine-tuned Piper TTS model, and can hold a full conversation using a hybrid offline/AI response system.

---

## Hardware

| Component | Detail |
|---|---|
| Device | Raspberry Pi 5 (BenderPi, 192.168.68.132) |
| Audio HAT | Adafruit Voice Bonnet (WM8960 codec) — I2S, 2x MEMS mics, 1W outputs |
| Speaker | 3W passive, connected to Voice Bonnet |
| LEDs | 12x WS2812B addressable RGB, GPIO 10 (SPI MOSI) |
| ALSA card | Card 2 (`seeed-2mic-voicecard`) |

---

## Operating Modes

Three modes, switchable at runtime:

| Mode | Service | Description |
|---|---|---|
| `clips` | `bender-wakeword` | Plays original Bender WAV clips on wake word |
| `tts` | `bender-tts` | Plays random TTS-generated lines on wake word |
| `converse` | `bender-converse` | Full conversation: STT → intent → response |

```bash
bash scripts/switch_mode.sh clips
bash scripts/switch_mode.sh tts
bash scripts/switch_mode.sh converse
bash scripts/switch_mode.sh status
```

---

## Quick Setup

### 1. Clone and configure

```bash
git clone git@github.com:Schmalvis/benderpi.git
cd benderpi
cp .env.example .env
# Fill in all values in .env
```

### 2. Install Python dependencies

```bash
python3 -m venv --system-site-packages venv
venv/bin/pip install -r requirements.txt
```

> `--system-site-packages` is required for hardware libraries (`lgpio`, `adafruit-blinka`, `neopixel`) installed system-wide via apt.

### 3. Download the Piper inference binary (aarch64)

```bash
mkdir -p piper
cd piper
curl -L https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz | tar xz
cd ..
```

### 4. Download the Bender voice model

From [Schmalvis/bender-tts-model](https://huggingface.co/Schmalvis/bender-tts-model) on Hugging Face:

```bash
mkdir -p models
cd models
wget https://huggingface.co/Schmalvis/bender-tts-model/resolve/main/bender.onnx
wget https://huggingface.co/Schmalvis/bender-tts-model/resolve/main/bender.onnx.json
cd ..
```

### 5. Pre-generate static TTS response library

```bash
python3 scripts/prebuild_responses.py
```

This creates `speech/responses/` with all pre-generated WAV files and `speech/responses/index.json`.

### 6. Install systemd services

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bender-wakeword  # or bender-converse for conversation mode
```

---

## Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `PORCUPINE_ACCESS_KEY` | Yes | Picovoice access key for wake word detection |
| `ANTHROPIC_API_KEY` | Converse mode only | Claude API key for AI fallback responses |
| `HA_TOKEN` | Converse mode only | Home Assistant long-lived access token |
| `HA_URL` | Converse mode only | HA base URL (default: `http://192.168.68.125:8123`) |
| `HA_WEATHER_ENTITY` | Optional | HA weather entity ID (default: `weather.forecast_home`) |
| `BENDER_AI_MODEL` | Optional | Claude model ID (default: `claude-haiku-4-5-20251001`) |

---

## Project Structure

```
benderpi/
├── .env                          — local secrets (not committed)
├── .env.example                  — template for .env
├── requirements.txt              — Python dependencies
├── BENDER_CONVERSATION_PLAN.md   — full architecture design doc
│
├── scripts/
│   ├── wake.py                   — clip mode: wake word + random Bender clip
│   ├── wake_tts.py               — TTS mode: wake word + random TTS line
│   ├── wake_converse.py          — converse mode: full conversation loop
│   ├── stt.py                    — speech-to-text (faster-whisper + webrtcvad)
│   ├── intent.py                 — intent classifier (keyword/regex rules)
│   ├── tts_generate.py           — Piper TTS wrapper (returns WAV path)
│   ├── ai_response.py            — Claude API fallback with Bender persona
│   ├── conversation_log.py       — JSON-Lines session/turn logger
│   ├── review_log.py             — log analysis + AI promotion suggestions
│   ├── prebuild_responses.py     — generates static TTS response WAV library
│   ├── switch_mode.sh            — switch between clips/tts/converse modes
│   ├── audio.py                  — WAV playback with real-time LED sync
│   ├── leds.py                   — WS2812B LED control
│   ├── leds_test.py              — standalone LED test
│   └── handlers/
│       ├── weather.py            — HA weather fetch → Bender response
│       └── ha_status.py          — parse HA confirmation → Bender reply
│
├── speech/
│   ├── metadata.csv              — Bender clip transcripts (LJSpeech format)
│   ├── greetings.txt             — clip filenames for random greeting selection
│   ├── tts_lines.txt             — TTS mode response lines
│   ├── wav/                      — original Bender WAV clips (not committed)
│   └── responses/
│       ├── index.json            — intent → WAV path mapping
│       ├── greeting/             — (uses real clips from speech/wav/)
│       ├── affirmation/          — (uses real clips from speech/wav/)
│       ├── dismissal/            — (uses real clips from speech/wav/)
│       ├── joke/                 — pre-generated TTS jokes (rebuilt by prebuild)
│       ├── personal/             — pre-generated personal Q&A (rebuilt by prebuild)
│       ├── ha_confirm/           — generic HA confirmation fallbacks
│       └── promoted/             — AI responses promoted to static (see below)
│
├── models/                       — ONNX voice model (not committed, ~61MB)
├── piper/                        — inference binary (not committed, ~51MB)
└── logs/                         — conversation logs (not committed, local only)
```

---

## Conversation System (Converse Mode)

### Flow

```
[Hey Bender detected]
        ↓
Play greeting clip (real Bender WAV)
        ↓
Listen → VAD silence detection → faster-whisper transcription
        ↓
Intent classification (keyword/regex, ~0ms)
        ↓
Response priority chain:
  1. Real Bender clip       → speech/wav/
  2. Pre-generated TTS      → speech/responses/<category>/
  3. Promoted TTS           → speech/responses/promoted/
  4. Dynamic handler        → weather.py / ha_status.py
  5. AI fallback (Claude)   → tts_generate.py
        ↓
Play response + LED sync
        ↓
Loop (8s silence timeout ends session)
```

### Intent Categories

| Intent | Triggers | Response source |
|---|---|---|
| GREETING | "hello", "hey", "how are you" | Real Bender clips |
| AFFIRMATION | "thanks", "great", "nice one" | Real Bender clips |
| DISMISSAL | "bye", "goodbye", "stop" | Real Bender clips — ends session |
| JOKE | "tell me a joke", "say something funny" | Real clips + pre-gen TTS |
| PERSONAL | "how old are you", "what do you eat" etc. | Pre-gen TTS (11 topics) |
| WEATHER | "weather", "is it raining", "forecast" | HA weather entity → TTS |
| HA_CONFIRM | "lights on", "temperature set to..." | HA status handler → TTS |
| PROMOTED | (custom patterns, see below) | Promoted TTS clips |
| UNKNOWN | Everything else | Claude API → TTS |

---

## Adding New Responses

### Adding to existing categories

Edit `scripts/prebuild_responses.py` and add to the relevant list/dict at the top:

```python
# Add a new joke
JOKE_RESPONSES = [
    ...
    "Your new joke text here.",
]

# Add a new personal question
PERSONAL_RESPONSES = {
    ...
    "new_topic": "Your Bender response here.",
}
```

Then regenerate:

```bash
python3 scripts/prebuild_responses.py
```

The index and WAV files update automatically. No service restart needed.

### Promoting frequent AI responses to static (reducing API usage)

When `review_log.py` identifies queries hitting the AI 3+ times, promote them:

1. Add an entry to `PROMOTED_RESPONSES` in `scripts/prebuild_responses.py`:

```python
PROMOTED_RESPONSES = [
    {
        "slug":    "meaning_of_life",              # filename-safe ID
        "pattern": r"meaning of life",             # regex matched against user input
        "text":    "Forty. No wait — it's bending. Everything is bending.",
    },
]
```

2. Run:

```bash
python3 scripts/prebuild_responses.py
```

The WAV is generated, `index.json` updated, and the pattern is checked before any AI call next session.

---

## Conversation Logging

Every session and turn is logged to `logs/YYYY-MM-DD.jsonl` (JSON Lines).

Each turn records: timestamp, session ID, turn number, user text, intent, sub-key, response method, response text, and model (if AI was called).

### Review logs

```bash
python3 scripts/review_log.py            # last 7 days
python3 scripts/review_log.py --days 30  # last 30 days
python3 scripts/review_log.py --all      # all time
```

Output includes:
- Local vs API usage breakdown
- Response method breakdown (real clip / pre-gen / handler / AI)
- Intent frequency
- Most common AI fallback queries, with `*** PROMOTE?` flag at ≥3 hits

---

## Voice Model

The Bender voice is a **Piper VITS** model fine-tuned from `en_US-lessac-medium` on 82 original Bender speech clips (cleaned, demucs-separated, resampled to 22050Hz).

Training: 5000 epochs, batch size 16, T4 GPU (Hugging Face Jobs).  
Model: [Schmalvis/bender-tts-model](https://huggingface.co/Schmalvis/bender-tts-model)  
Dataset: [Schmalvis/bender-tts-dataset](https://huggingface.co/datasets/Schmalvis/bender-tts-dataset)

---

## Service Management

```bash
# Switch modes
bash scripts/switch_mode.sh clips
bash scripts/switch_mode.sh tts
bash scripts/switch_mode.sh converse
bash scripts/switch_mode.sh status

# Manage services directly
sudo systemctl start|stop|restart bender-converse
journalctl -u bender-converse -f       # live logs

# Rebuild response library (after editing prebuild_responses.py)
python3 scripts/prebuild_responses.py

# Review conversation logs
python3 scripts/review_log.py
```

---

## Audio Setup

- Driver: `seeed-voicecard` (HinTak fork, v6.12) via DKMS
- ALSA default: card 2 (WM8960)
- Both playback and capture on card 2, device 0

### Persist volume across reboots

```bash
amixer -c 2 sset Speaker 85%
sudo alsactl store
sudo cp /var/lib/alsa/asound.state /etc/voicecard/wm8960_asound.state
```

---

## LED Behaviour

12x WS2812B on SPI (GPIO 10). During audio playback, LEDs flash with brightness proportional to audio amplitude — amplitude-reactive in real time.

SPI must be enabled: `dtparam=spi=on` in `/boot/firmware/config.txt`

---

## History

- Originally fitted with Pimoroni Audio AMP SHIM (MAX98357A) — replaced with Adafruit Voice Bonnet for microphone input
- GPIO data line moved from GPIO 23 → GPIO 10 (SPI MOSI) for Pi 5 compatibility
- TTS voice model trained March 2026 via HF Jobs (Docker, pytorch/pytorch image)
- Conversational mode added March 2026 with hybrid offline/AI response system
