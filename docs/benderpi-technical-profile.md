# BenderPi — Technical Profile

A hardware and software specification for the BenderPi voice assistant, intended for external review to identify hardware upgrade opportunities.

**Last updated:** 2026-03-16

---

## 1. What BenderPi Does

A standalone voice assistant running on a Raspberry Pi 5. Responds to a wake word ("Hey Bender"), holds multi-turn conversations, controls smart home devices via Home Assistant, gives weather/news briefings, and falls back to the Claude API for open-ended queries. All speech output uses a custom Piper TTS voice model trained on the character Bender from Futurama.

**Primary use pattern:** Wake word → listen → classify intent → respond (local clip or generated TTS) → loop until silence timeout. Responses prioritise local/pre-generated audio (instant) over API-generated (2-5s latency).

---

## 2. Current Hardware

| Component | Spec | Notes |
|---|---|---|
| **SBC** | Raspberry Pi 5 (4GB or 8GB RAM) | Quad-core Arm Cortex-A76 @ 2.4GHz, VideoCore VII GPU |
| **OS** | Raspberry Pi OS (Debian trixie, aarch64) | 64-bit, Linux 6.x kernel |
| **Audio HAT** | Adafruit Voice Bonnet (WM8960 codec) | I2S interface, 2x MEMS microphones, 1W speaker outputs |
| **Speaker** | 3W passive speaker | Connected to Voice Bonnet speaker terminals |
| **LEDs** | 12x WS2812B addressable RGB | Data on GPIO 10 (SPI MOSI), SPI-driven via neopixel_spi |
| **Storage** | microSD card (default) | Standard Raspberry Pi OS boot |
| **Power** | USB-C 5V/5A (official Pi 5 PSU) | ~5W idle, ~8W under load |
| **Network** | Wi-Fi (on-board) | Used for Home Assistant API, Claude API, BBC RSS |
| **PCIe** | Gen 2 x1 lane available (FFC connector) | Currently unused — available for NPU/NVMe |
| **GPIO used** | GPIO 10 (SPI MOSI for LEDs), I2S pins for Voice Bonnet | Most GPIO available |

### Audio Hardware Constraints

The WM8960 codec on the Voice Bonnet can only operate at **one sample rate at a time**. Microphone input (wake word detection, STT) runs at 16kHz. Speaker output (playback) runs at 44.1kHz. Input and output streams cannot be open simultaneously — the system must close the output stream before listening and vice versa. This creates a hard constraint on simultaneous listen/speak operations.

**ALSA configuration:** Card 2 (`seeed-2mic-voicecard`, `hw:2,0`). Volume set via `amixer -c 2 sset Speaker N%`.

---

## 3. Current Software Stack

### Core Pipeline

| Stage | Technology | Model/Config | Runs on | Latency |
|---|---|---|---|---|
| **Wake word** | Porcupine (Picovoice) | Custom "Hey Bender" model (.ppn) | CPU | <50ms |
| **Voice Activity Detection** | webrtcvad-wheels | Aggressiveness level 2, 30ms frames | CPU | ~0ms |
| **Speech-to-text** | faster-whisper (CTranslate2) | `tiny.en` (INT8 quantised) | CPU | ~1-2s |
| **Intent classification** | Regex/keyword matching | ~50 patterns across 10 intent types | CPU | <1ms |
| **Text-to-speech** | Piper TTS (VITS ONNX) | Custom fine-tuned Bender voice model | CPU | ~0.5-1.5s |
| **AI fallback** | Claude API (Haiku) | 150 max tokens, Bender persona prompt | Cloud | ~2-3s (network) |
| **Audio playback** | PyAudio (PortAudio) | 44.1kHz, 16-bit, mono | WM8960 DAC | Real-time |
| **LED control** | neopixel_spi (Adafruit) | SPI at GPIO 10, amplitude-reactive | SPI bus | Real-time |

### Supporting Services

| Component | Technology | Purpose |
|---|---|---|
| **Home Assistant** | REST API (urllib) | Smart home control (lights, switches, climate) |
| **Weather** | HA weather entity | Current conditions + forecast, cached 30min |
| **News** | BBC RSS feeds (UK + England) | Headlines, cached 2h |
| **Conversation logging** | JSON Lines files | Per-session turn logging to `logs/YYYY-MM-DD.jsonl` |
| **Metrics** | Custom JSONL writer | Timer + counter events to `logs/metrics.jsonl` |
| **Health watchdog** | Custom Python | Anomaly detection on metrics (STT empty rate, latency, errors) |
| **Web UI** | FastAPI + vanilla HTML/CSS/JS | Admin panel, puppet mode, dashboard, config editor |
| **Auto-deploy** | systemd timer + git pull | Polls GitHub every 5 min, restarts on changes |

### Python Environment

- Python 3.13 (system) with `--system-site-packages` venv
- Key packages: `pvporcupine`, `faster-whisper`, `webrtcvad-wheels`, `PyAudio`, `anthropic`, `scipy`, `numpy`, `fastapi`, `uvicorn`
- Hardware-only packages (system-level, not pip): `lgpio`, `adafruit-blinka`, `neopixel-spi`

### Piper TTS Model

- Architecture: VITS (Variational Inference with adversarial learning for end-to-end Text-to-Speech)
- Base model: `en_US-lessac-medium`
- Fine-tuned on 82 original Bender speech clips (cleaned, demucs-separated, resampled to 22050Hz)
- Training: 5000 epochs, batch size 16, T4 GPU
- Output: 22050Hz mono, resampled to 44.1kHz via scipy for playback
- Format: ONNX (inference via Piper aarch64 binary)
- Model hosted at: [Schmalvis/bender-tts-model](https://huggingface.co/Schmalvis/bender-tts-model)

---

## 4. Performance Bottlenecks & Limitations

| Bottleneck | Current state | Impact |
|---|---|---|
| **STT accuracy** | Whisper `tiny.en` — fast but low accuracy | Misheard words, false intent matches |
| **STT latency** | ~1-2s for recording + transcription | Noticeable pause before Bender responds |
| **TTS latency** | ~0.5-1.5s per dynamic response | Delay on AI fallback and HA control responses |
| **API dependency** | Claude Haiku for ~10-20% of queries | Network latency, cost, privacy |
| **Single sample rate** | WM8960 can't listen and speak simultaneously | Can't do barge-in (interrupt Bender mid-response) |
| **Intent accuracy** | Regex-only, no ML | False positives on ambiguous inputs, can't handle paraphrases |
| **No barge-in** | Must wait for Bender to finish before speaking | Unnatural conversation flow |
| **Single speaker** | 3W passive, mono | Limited audio quality and volume |
| **No AEC** | No acoustic echo cancellation | Can't listen while playing (hardware + software limitation) |

---

## 5. Computational Resource Usage

| Resource | Wake word listening | During conversation |
|---|---|---|
| **CPU** | ~5% (Porcupine) | ~40-80% spikes (Whisper, Piper, scipy resample) |
| **RAM** | ~200MB (Python + Whisper model loaded) | ~400MB peak (during TTS generation) |
| **GPU** | Unused | Unused |
| **PCIe** | Unused | Unused |
| **NPU** | N/A (none installed) | N/A |
| **Disk I/O** | Minimal (log writes) | Moderate (temp WAV files, model loading) |

---

## 6. What We'd Like to Improve

Listed in priority order:

1. **STT accuracy** — run a larger Whisper model (base.en, small.en, or distil-whisper) without increasing latency
2. **Response latency** — reduce end-to-end time from wake word to first audio output
3. **Local AI** — reduce or eliminate Claude API dependency for open-ended queries
4. **Intent classification** — move from regex to ML-based classification that handles paraphrases
5. **Simultaneous listen/speak** — enable barge-in (interrupt Bender while speaking)
6. **Audio quality** — better speaker, stereo, or audio processing
7. **TTS speed** — faster Piper inference or alternative TTS
8. **Wake word** — migrate from proprietary Porcupine to open-source (OpenWakeWord)

---

## 7. Interfaces Available for Hardware Additions

| Interface | Status | Notes |
|---|---|---|
| **PCIe Gen 2 x1** | Free | FFC connector on Pi 5. Could host NPU (e.g. Hailo AI HAT+) or NVMe SSD. Cannot use both simultaneously without a switch. |
| **USB 3.0** (x2) | Free | Could host USB accelerator (Coral TPU, etc.) or USB audio device |
| **USB 2.0** (x2) | Free | Lower bandwidth, suitable for USB mic or simple peripherals |
| **GPIO** | Mostly free | GPIO 10 used for LEDs (SPI), I2S pins used by Voice Bonnet. ~20 GPIO available |
| **I2C** | Available | Voice Bonnet uses some I2C, but bus is shareable |
| **SPI** | In use (LEDs) | SPI0 CE0 used for WS2812B. SPI1 available |
| **UART** | Available | Not currently used |
| **Camera (CSI)** | Free | Could add Pi Camera for visual features |

---

## 8. Existing Analysis

A detailed feasibility study of the Raspberry Pi AI HAT+ and AI HAT+ 2 (Hailo NPU accelerators) is available at `docs/ai-hat-plus-analysis.md` in the project repository. Key finding: the NPU's strengths (vision CNNs) don't align well with BenderPi's current workloads (Transformers, autoregressive generation), though Raspberry Pi's own documentation specifically identifies "local voice-to-action agents" as a target use case for the AI HAT+ 2.

---

## 9. Environment & Constraints

- **Location:** Home in Nottingham, England. Indoor use only.
- **Network:** Home Wi-Fi (192.168.68.x). Home Assistant at 192.168.68.125.
- **Users:** Single household. Primary user is an adult; a young child also interacts with the device.
- **Power:** Standard UK mains via USB-C PSU. No battery/portable requirement.
- **Budget:** Hobby project. Cost-conscious but willing to invest in meaningful improvements.
- **Form factor:** Currently open/exposed on a desk. No enclosure constraints.
- **Noise environment:** Typical home (TV, other people talking). No industrial noise.
