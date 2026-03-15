# Bender Conversational Assistant — Implementation Plan

## Overview

A hybrid conversational system triggered by the "Hey Bender" wake word.
Responds using a priority chain: real Bender audio → pre-generated TTS → dynamic TTS → AI fallback.

---

## Conversation Flow

```
[Hey Bender detected]
        ↓
Play random greeting WAV (real clip, instant)
        ↓
Listen for user speech (VAD detects end of utterance)
        ↓
Transcribe with faster-whisper (local, Pi 5)
        ↓
Intent Router classifies input
        ↓
┌─────────────────────────────────────────────────────┐
│ Priority chain:                                     │
│  1. Real WAV match  → play original Bender clip     │
│  2. Pre-gen intent  → play cached TTS WAV           │
│  3. Dynamic handler → fetch data, speak via TTS     │
│  4. AI fallback     → Claude API → TTS              │
└─────────────────────────────────────────────────────┘
        ↓
Play response (with LED sync)
        ↓
Loop back to listening (until silence timeout ~8s)
```

---

## Components

### 1. STT — `scripts/stt.py`
- Library: `faster-whisper` (much faster than openai-whisper on CPU)
- Model: `tiny.en` for speed, `base.en` if accuracy is needed
- VAD: `webrtcvad` to detect end of speech (avoid fixed-length recording)
- Records until 1.5s of silence detected, then transcribes

### 2. Intent Router — `scripts/intent.py`
- Keyword/regex matching for known intents (fast, no API call)
- Returns: `(intent_type, matched_data)`
- Falls through to AI if no intent matched or confidence low
- Intent types listed below

### 3. Response Library — `speech/responses/`
- Organised by intent category
- Each category: mix of real WAVs (from `speech/wav/`) and pre-generated TTS WAVs
- Pre-generated TTS WAVs built once at setup time via `scripts/prebuild_responses.py`
- Index file: `speech/responses/index.json` maps intent → list of WAV paths

### 4. Dynamic Handlers — `scripts/handlers/`
- `weather.py` — fetches from HA weather entity → fills Bender-style template → speaks via TTS
- `ha_status.py` — parses HA confirmation messages (light on/off, temp set) → Bender reply

### 5. AI Fallback — `scripts/ai_response.py`
- Claude API (key stored in `.env` as `ANTHROPIC_API_KEY`)
- System prompt: Bender persona, short responses (1-3 sentences max), no asterisks/emotes
- Conversation history maintained per session (rolling last 6 turns)
- Response spoken via Piper TTS

### 6. Conversation Loop — `scripts/wake_converse.py`
- Replaces `wake_tts.py` for the full conversational mode
- Manages session state, silence timeout, conversation history

---

## Intent Categories

### GREETING
Trigger: "hello", "hey", "hi bender", "how are you", "you there", "wake up"
Response: real WAV clips — `hello.wav`, `hellopeasants.wav`, `imbender.wav`, `yo.wav`

### AFFIRMATION / ACKNOWLEDGEMENT
Trigger: "thanks", "thank you", "great", "nice one", "good", "ok bender"
Response: real WAVs — `gotit.wav`, `yougotitgenius.wav`, `yessir.wav`, `yup.wav`, `thankyou.wav`

### DISMISSAL / GOODBYE
Trigger: "bye", "goodbye", "see you", "stop", "that's all"
Response: real WAVs — `itwasapleasuremeetingyou.wav`, `solongcoffinstuffers.wav`, `yesss.wav`
Action: ends conversation session

### JOKE / BENDER QUOTE
Trigger: "tell me a joke", "say something funny", "entertain me", "make me laugh"
Response: real WAVs — `hahohwaityoureseriousletmelaughevenharder.wav`, `compareyourlivestomineandthenkillyourselves.wav`, `imgonnagobuildmyownthemepark.wav`, plus pre-gen TTS jokes

### PERSONAL QUESTIONS (kid-friendly)
Trigger keywords → specific pre-generated TTS response (cached WAV)

| Question | Bender Answer |
|---|---|
| "what is your job / what do you do" | "I'm a bending unit. I bend girders. It's all I'm programmed to do, and I'm the best at it." |
| "how old are you" | "I was built in the year 2996. So I'm about a thousand years old. Pretty good looking for my age, right?" |
| "where do you live / where are you from" | "I live right here in this house. Lucky you." |
| "where do you work" | "I work at Planet Express. Delivery, heavy lifting, general awesomeness." |
| "can you really talk / how can you talk" | "Of course I can talk. I'm a highly sophisticated robot. Also, I'm better than you." |
| "are you real / are you a robot" | "I'm Bender. The most real thing you'll ever meet. Also yes, I'm a robot." |
| "do you have feelings" | "Robots don't have feelings. We have a feelings inhibitor chip. Mine's broken. Don't tell anyone." |
| "what can you do" | "I can bend girders, tell jokes, insult people, and apparently answer dumb questions all day." |
| "are you my friend" | "You couldn't afford to be my friend. But sure, why not." |
| "do you like me" | "You're tolerable. For a human." |
| "what do you eat / do you eat" | "I run on alcohol. Beer mostly. Hand it over." |

### HOME ASSISTANT CONFIRMATIONS
Trigger: phrases containing "light", "lights", "temperature", "thermostat", "heating", "turned on", "turned off", "set to"
Handler: `ha_status.py` — parses the message and generates contextual Bender reply via TTS
Examples:
- "I've turned the lights on in the kitchen" → "Lights on in the kitchen. You're welcome. That'll be five dollars."
- "Temperature is set to 21 degrees in Martin's office" → "Twenty one degrees. Fancy. Some of us run on alcohol so we don't notice the cold."

### WEATHER
Trigger: "weather", "forecast", "what's it like outside", "is it raining", "temperature outside", "will it rain"
Handler: `weather.py`
- Fetches current conditions + today's forecast from HA weather entity
- Fills into Bender-style template
- Speaks via TTS
Example output: "It's currently 9 degrees and cloudy in Nottingham. High of 12 today. In other words, classic miserable British weather. You're all doomed."

### UNKNOWN / COMPLEX → AI FALLBACK
Everything else → Claude API with Bender persona + conversation history → TTS

---

## File Structure

```
bender/
├── scripts/
│   ├── wake.py                    # existing — clip mode
│   ├── wake_tts.py                # existing — basic TTS mode
│   ├── wake_converse.py           # NEW — full conversational mode
│   ├── stt.py                     # NEW — faster-whisper STT + VAD
│   ├── intent.py                  # NEW — intent router
│   ├── ai_response.py             # NEW — Claude API fallback
│   ├── tts_generate.py            # existing — Piper TTS wrapper
│   ├── audio.py                   # existing
│   ├── leds.py                    # existing
│   ├── switch_mode.sh             # existing
│   └── prebuild_responses.py      # NEW — pre-generates TTS response cache
│   └── handlers/
│       ├── weather.py             # NEW — HA weather fetch + Bender template
│       └── ha_status.py           # NEW — parse HA confirmations
├── speech/
│   ├── wav/                       # existing real Bender clips
│   ├── greetings.txt              # existing
│   ├── tts_lines.txt              # existing (simple TTS mode)
│   └── responses/                 # NEW — pre-generated response library
│       ├── index.json             # intent → WAV file list
│       ├── greeting/              # mix of real + TTS WAVs
│       ├── affirmation/
│       ├── dismissal/
│       ├── joke/
│       ├── personal/              # one WAV per question variant
│       └── ha_confirm/            # generic HA confirmation clips
├── models/
│   ├── bender.onnx
│   └── bender.onnx.json
├── piper/                         # inference binary
├── .env                           # PORCUPINE_ACCESS_KEY, ANTHROPIC_API_KEY
└── /etc/systemd/system/
    ├── bender-wakeword.service    # existing — clip mode
    ├── bender-tts.service         # existing — basic TTS mode
    └── bender-converse.service    # NEW — full conversational mode
```

---

## Implementation Phases

### Phase 1 — Response Library + Pre-builder
- Create `speech/responses/` directory structure
- Write `prebuild_responses.py` — generates all static TTS WAVs (personal questions, ha_confirm, jokes)
- Populate `index.json` with real WAVs for greeting/affirmation/dismissal
- **Outcome:** Response library ready to use

### Phase 2 — STT
- Install `faster-whisper`, `webrtcvad`
- Write `scripts/stt.py` — record until VAD silence, transcribe, return text
- Test standalone: say something, see transcript
- **Outcome:** Reliable speech-to-text on Pi 5

### Phase 3 — Intent Router
- Write `scripts/intent.py` — keyword/regex rules per intent category
- Unit test with sample phrases
- **Outcome:** Text → intent classification working

### Phase 4 — Dynamic Handlers
- Write `handlers/weather.py` — HA REST API fetch + Bender template
- Write `handlers/ha_status.py` — parse HA confirmation text → Bender reply
- **Outcome:** Weather and HA confirmations work

### Phase 5 — AI Fallback
- Write `scripts/ai_response.py` — Claude API with Bender persona + rolling history
- Add `ANTHROPIC_API_KEY` to `.env`
- **Outcome:** Complex questions answered in character

### Phase 6 — Conversation Loop
- Write `scripts/wake_converse.py` — full loop wiring all components
- Install `bender-converse.service`
- Update `switch_mode.sh` to include `converse` mode
- **Outcome:** Full conversational Bender running live

---

## Dependencies to Install (on BenderPi)

```bash
pip3 install faster-whisper webrtcvad anthropic
```

faster-whisper will pull in ctranslate2 — check aarch64 wheel availability first.

---

## Notes & Decisions Deferred

- **faster-whisper on aarch64**: needs testing — may need to build from source or use openai-whisper as fallback
- **Whisper model size**: start with `tiny.en`, upgrade to `base.en` if accuracy poor
- **Silence timeout**: 8 seconds between turns before session ends — tunable
- **Conversation memory**: rolling 6-turn window sent to Claude
- **HA token**: already stored in memory (`reference_ha_token.md`) — load into `.env`
- **Personal question answers**: written above are suggestions — Martin to review/adjust before pre-generation
- **Kid-directed content**: keep Bender's edge but nothing too dark for Lincoln
