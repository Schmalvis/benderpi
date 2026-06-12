# BenderPi — Project Brief for Architectural Review

---

## 1. Project Overview

BenderPi is a Raspberry Pi 5 voice assistant with the personality of Bender Bending Rodriguez (Futurama). It responds to the wake word "Hey Bender", engages in multi-turn conversation, controls Home Assistant smart home devices, delivers weather and news briefings, and falls back to the Claude API for queries outside its local knowledge.

**GitHub:** `git@github.com:Schmalvis/benderpi.git`

**Design philosophy — offline-first and privacy-preserving.** BenderPi is designed to operate entirely without internet connectivity for core functionality. The Hailo AI HAT+ (Hailo-10H, 40 TOPS NPU) provides dedicated on-device inference for STT (Whisper), LLM (Qwen2.5-1.5B), and VLM (Qwen2-VL-2B). Cloud APIs are a last resort, only invoked when local quality checks fail (hedge phrases, too-short responses).

**Current maturity level:** Production-deployed on the home network via a systemd service with auto-deploy from `main`. Core voice pipeline (wake word → STT → intent → response → TTS) is fully operational. The Hailo NPU LLM path is active; Hailo STT and VLM are currently disabled due to KV-Cache contention. A web-based admin UI (FastAPI + Svelte) is deployed on a companion service. The immediate critical issue is the Picovoice free-tier sunset on **June 30, 2026** — see Section 9.

**Home Assistant integration** covers dynamic entity discovery (lights, switches, climate/TRV), weather briefings via HA weather entities, and bidirectional IPC between the voice service and the web UI.

---

## 2. Hardware Stack

| Component | Role | Key Constraint |
|---|---|---|
| Raspberry Pi 5 | Host SBC, runs all services | aarch64, Debian trixie |
| Hailo AI HAT+ (Hailo-10H, 40 TOPS NPU) | On-device inference for LLM, STT, VLM | KV-Cache is a shared resource — only one model can hold it at a time |
| Raspberry Pi AI Camera (IMX500) | RGB frame capture for VLM / scene analysis | Used as a plain RGB camera only; IMX500 on-chip NPU not utilised |
| WM8960 codec (audio HAT) | I2S audio: mic input (16kHz) + speaker output (44.1kHz) | **Single sample rate at a time** — mic and playback cannot coexist |
| ReSpeaker XVF3800 4-mic array | Primary microphone (USB) | Exposes stereo ALSA device; left channel extracted in software for 16kHz mono input to wake word engine |
| WS2812B LEDs (45 units) | Visual status indicator | SPI MOSI / GPIO 10; driven by `neopixel_spi` |
| 3W passive speaker | Audio output | Connected to WM8960 codec |

### HARD CONSTRAINTS

1. **WM8960 single sample rate:** The codec can only operate at one sample rate simultaneously. Mic input (Porcupine/STT) requires 16000 Hz; playback requires 44100 Hz. The output stream must be explicitly opened (`audio.open_session()`) only after wake word detection and closed (`audio.close_session()`) at the end of every session before mic input resumes. A missed `close_session()` on any exception path will lock the device at 44100 Hz, breaking wake-word detection on the next cycle.

2. **Hailo KV-Cache exclusivity:** The Hailo-10H has a single KV-Cache shared across all NPU models. STT (Whisper HEF), LLM (Qwen2.5-1.5B HEF), and VLM (Qwen2-VL-2B HEF) cannot hold it simultaneously. The conversation loop must sequence: acquire STT → transcribe → `stt.release()` → acquire LLM → infer → `ai_local.release_chip()`. This contract is enforced by convention only, with no lock or semaphore.

3. **SPI LED bus:** `pixels.show()` is a synchronous SPI write called on every 512-frame audio chunk (~11.6 ms). SPI contention between LED updates and other SPI peripherals could cause audio stutter.

4. **Single shared PyAudio instance:** `pyaudio.PyAudio()` is instantiated once at module import in `audio.py`. Concurrent initialisation crashes PortAudio on this platform. All mic access must use `audio.get_pa()`.

### CLAUDE.md Discrepancies vs Current Code

| Topic | CLAUDE.md states | Current code / subsystem summary |
|---|---|---|
| Microphone hardware | "Adafruit Voice Bonnet (WM8960 codec) — I2S, 2x MEMS mics" | Primary mic is **ReSpeaker XVF3800 4-mic USB array**. WM8960 handles codec/speaker only. Device discovery hints include `xvf_dsnoop` (reSpeaker) as the preferred input device. |
| ALSA card | "Card 2 (`seeed-2mic-voicecard`, `hw:2,0`)" | `seeed` is now a legacy fallback hint; `mic_shared`/`xvf_dsnoop` (reSpeaker) is the active device. |
| LED count | "12x WS2812B" | Code uses `led_count = 45` as the config default. |
| `audio.play()` silence | "50ms pre-silence + clip + 200ms post-silence" | Current code uses `SILENCE_PRE = 20ms` and `SILENCE_POST = 80ms`. Values were tuned down. |
| VAD silence detection | "records until 1.5s silence" | Current code: 15 × 30ms frames = **450ms** silence threshold. |
| Handler structure (CLAUDE.md project tree) | `handlers/ha_control.py`, `handlers/weather.py` | HA control was refactored (2026-05-24/25) into `entity_registry.py`, `entity_matcher.py`, `ha_client.py`, `ha_control.py`, `ha_handler.py`. Many new handler files added under `handlers/`. |
| Intent list | Missing TIMER, TIME, VISION, CONTEXTUAL intents | These are all implemented and active in `intent.py`. |
| `response_hard_timeout_s` | Listed as `20` in config table | Actual config file value is `45` per the AI routing subsystem summary. |

---

## 3. Software Architecture

### Module Map

```
wake_converse.py (thin orchestrator, ~120 lines)
    ├── stt.py              ← audio.get_pa(), hailo_platform.genai.Speech2Text
    ├── intent.py           ← regex/keyword classifier; no external deps
    ├── session.py          ← ConversationSession (turn loop, IPC, vision injection)
    │     ├── responder.py  ← handler registry + AI routing
    │     │     ├── handlers/
    │     │     │     ├── clip_handler.py, pregen_handler.py, promoted_handler.py
    │     │     │     ├── contextual_handler.py, weather_handler.py, news_handler.py
    │     │     │     ├── time_handler.py, ha_handler.py, timer_handler.py, vision_handler.py
    │     │     │     └── timer_alert.py (standalone runner, not a Handler subclass)
    │     │     ├── ai_response.py   ← Claude API (Haiku, streaming)
    │     │     └── ai_local.py      ← LocalAIResponder → _HailoLLMResponder / _OllamaResponder
    │     ├── tts_generate.py  ← PiperPool (3 persistent processes), resampling, streaming
    │     └── vision.py        ← camera.py → vlm.py (disabled by config)
    ├── audio.py            ← PyAudio session management, playback, LED callback
    ├── leds.py             ← WS2812B SPI control
    ├── briefings.py        ← TTL-cached weather/news/time WAV generation
    ├── timers.py           ← named timers/alarms, persistence, check_fired()
    ├── time_parser.py      ← natural language duration/alarm parsing
    └── config.py           ← singleton config from bender_config.json + .env

scripts/web/app.py + routes/ (FastAPI, 9 domain routers)
    ├── auth.py             ← PIN middleware
    └── routes/             ← actions, config, logs, puppet, remote, status, timers, vision, ws

Supporting:
    config.py, logger.py, metrics.py, watchdog.py, generate_status.py
```

### Key Design Patterns

**Handler registry (`responder.py`):** `Responder.__init__()` instantiates all `Handler` subclasses, builds a `dict[intent_name → list[Handler]]` dispatch table, and iterates it on each turn. Handlers return `Response`, `ResponseStream`, or `None` (fall through). This decouples intent routing from handler implementation and makes adding/removing handlers trivial.

**ConversationSession (`session.py`):** Encapsulates the per-session turn loop, IPC file management, vision context injection, and response playback. The outer `wake_converse.py` loop is reduced to wake word detection + session lifecycle. `TurnResult` communicates whether the session should end and why.

**BriefingSource (`briefings.py`):** A dataclass that pairs a `generate_text: Callable` with a TTL, WAV path, and fallback text. `_get_briefing_wav()` implements the check-generate-fallback flow generically for all briefing types (weather, news, time). Thread-safe via `_meta_lock`.

**Hybrid AI routing (`responder.py`, `ai_local.py`):** Three-tier routing: Hailo NPU → Ollama CPU → Claude API. A `check_response_quality()` gate after each local inference triggers escalation on hedge phrases or too-short responses. `QualityCheckFailed` exception carries the reason; the `Responder` decides whether to escalate or force-use the local answer based on `ai_backend` config.

**IPC files:** Three files coordinate between the voice service, web UI, and the running session: `session_file` (JSON state of the active session), `end_session_file` (presence triggers remote session end), `abort_file` (presence triggers mid-playback audio abort). This avoids any IPC framework dependency.

**Audio callback decoupling:** `audio.play()` accepts an `on_chunk` callback (normalised amplitude) rather than having a direct dependency on `leds.py`. `wake_converse.py` wires these together, keeping audio and LEDs independently testable.

**Persistent Piper process pool:** `PiperPool` maintains 3 persistent `_PiperProcess` subprocesses using `--json-input` mode. Synthesis requests are serialised via `queue.Queue`. Each worker auto-restarts on crash. This eliminates per-call subprocess spawn overhead (~300ms+).

---

## 4. Full Pipeline: Wake to Response

### Complete numbered walkthrough

**1. Wake word detection** — `wake_converse.py:wait_for_wakeword()`
- Creates a `pvporcupine` instance with `hey-bender.ppn` model.
- Opens mic stream via shared `audio.get_pa()` at 16000 Hz.
- If device is `xvf_dsnoop` (reSpeaker): opens stereo (2ch), extracts left channel only (`[::2]`).
- Reads frames of `porcupine.frame_length` samples (~512), calls `porcupine.process()`.
- Stall detection: if no PCM reads for `cfg.wake_stall_seconds` (30s), raises `RuntimeError("wake loop stalled")` → outer loop reinits.
- Systemd watchdog ping every `cfg.wake_heartbeat_frames` (250) frames.
- **Latency contribution:** negligible (polling loop); ~5s on first run before `warm_up()` completes in background.

**2. Session start** — `wake_converse.py:main()` + `session.py:ConversationSession.start()`
- Porcupine stream closed; `porcupine.delete()` called.
- `audio.open_session()` opens 44100 Hz output stream + writes 100ms warm-up silence.
- Greeting clip selected and played (`audio.play()`).
- `FutureVisionProvider.start_capture()` submits `vision.analyse_scene()` to background thread (if VLM enabled).
- Session file written to `cfg.session_file`.
- **Latency contribution:** ~100ms (DAC warm-up) + clip playback duration.

**3. STT recording** — `stt.py:listen_and_transcribe()` (called each turn)
- Mic stream opened at 16000 Hz via shared `_pa`.
- 200ms post-playback mic buffer discarded (reverb flush via `cfg.post_play_flush_ms`).
- VAD (webrtcvad, aggressiveness 3) gates recording. 30ms frames; silence detected after 15 consecutive silent frames (450ms).
- Max recording: 15s hard limit.
- **Latency contribution:** recording duration (user speech) + 450ms silence detection at end.

**4. STT transcription** — `stt.py` (Hailo or CPU fallback)
- **Hailo path (disabled by default):** `hailo_platform.genai.Speech2Text` on Hailo-10H NPU.
- **CPU fallback (active):** `faster-whisper` with `tiny.en` model (configurable).
- Hallucination filtering: removes known phantom phrases, repetitive-character patterns (`(.)\1{5,}`), outputs > 200 chars.
- `stt.release()` called after transcription to free Hailo KV-Cache.
- **Latency contribution:** ~1–2s (faster-whisper CPU, tiny.en); first call ~5s without `warm_up()`.

**5. Intent classification** — `intent.py:classify(text)`
- Pure regex/keyword matching, no ML.
- Dispatch order: `HA_CONTROL` → `TIMER*` → `WEATHER` → `NEWS` → `TIME` → `DISMISSAL` → `JOKE` → `VISION` → `CONTEXTUAL` → `PERSONAL` → `PROMOTED` → `GREETING` → `AFFIRMATION` → `UNKNOWN`.
- `GREETING`/`AFFIRMATION` only match if utterance ≤ `cfg.simple_intent_max_words` (guards against "hey, turn on the lights" matching `GREETING`).
- **Latency contribution:** negligible (<1ms).

**6. Handler dispatch** — `responder.py:get_response()`
- `Responder` looks up intent in dispatch table, calls handlers in registration order.
- First handler to return non-`None` wins.
- For pre-built intents (`GREETING`, `PERSONAL`, `WEATHER`, `NEWS`, `TIME`, `HA_CONTROL`, `TIMER*`, `VISION`, `CONTEXTUAL`): handled without entering the AI routing path.
- **Latency (static WAV handlers):** ~0ms file lookup.
- **Latency (briefing handlers):** ~0ms on cache hit; ~3–10s on miss (TTS generation).
- **Latency (HA_CONTROL):** HA REST API call + entity match + TTS confirm; ~500ms–2s.
- **Latency (TIMER handlers):** time parsing + TTS; ~1–3s.

**7. AI routing (UNKNOWN intent)** — `responder.py:_respond_ai()`
- `effective_routing` determined from `ai_backend` config + per-scenario rules.
- **Step A — Hailo NPU (Qwen2.5-1.5B):** `ai_local.generate(text)` → `_HailoLLMResponder`. If HEF missing or init failed (60s cooldown), raises `RuntimeError` → falls through to Ollama.
- **Step B — Ollama CPU (Qwen2.5:1.5b):** `http://localhost:11434/api/chat`, 25s timeout. `warm_up()` pre-loads model at service start.
- **Step C — Quality check:** after A or B, `check_response_quality()` checks length (<10 chars) and hedge phrases. Raises `QualityCheckFailed` → escalates to Step D unless `local_only`.
- **Step D — Claude API (Haiku):** `ai_response.py:respond_streaming()` yields sentences as generated. Concurrent TTS via `tts_generate.speak_from_iter()`.
- **Latency (Hailo NPU):** ~3–8s (on-chip inference; first call includes VDevice init).
- **Latency (Ollama CPU):** up to 25s hard timeout; typically 5–15s.
- **Latency (Claude API):** 5s connect + streaming; first audio sentence available ~3–6s after request.

**8. Thinking sound** — `session.py:handle_turn()`
- Inference thread launched. After 150ms join timeout, if still running and `cfg.thinking_sound` enabled, a random thinking clip is played.
- **Current gap:** clip plays after `get_response()` returns, not during generation — audio cue fires too late to be useful.

**9. TTS generation** — `tts_generate.py`
- `speak_from_iter(sentence_iter)` for streaming responses: submits each sentence to `PiperPool` as it arrives from LLM; yields WAV paths in order.
- `speak(text)` for pre-known text: splits on sentence boundaries, submits all concurrently, returns single WAV.
- Piper output: 22050Hz → `scipy.signal.resample_poly(2, 1)` → 44100Hz.
- De-essing: 2nd-order Butterworth high-pass at 7kHz applied post-resample.
- **Latency per sentence:** ~1–3s (Piper inference); pool parallelism means sentence 2 synthesises while sentence 1 plays.

**10. Playback** — `audio.py:play()` or `play_stream()`
- 20ms pre-silence + WAV data + 80ms post-silence per clip.
- `on_chunk` callback fires per 512-frame chunk (~11.6ms); wired to `leds.set_level()`.
- Abort flag checked per-chunk; `audio.abort()` stops playback immediately.
- **Latency contribution:** clip duration + 100ms overhead per response.

**11. Session continue / end**
- If `DISMISSAL` intent and `cfg.dismissal_ends_session`: fast-path exit (no audio played).
- After playback: 200ms reverb flush before next STT cycle.
- Session timeout: `cfg.silence_timeout` seconds of consecutive empty STT → `session.end("timeout")`.
- `audio.close_session()` frees output stream → mic can resume.
- `ai_local.release_chip()` frees Hailo KV-Cache for next STT cycle.

---

## 5. Software Dependencies

### Wake Word

| Package | Version | Purpose | Risk |
|---|---|---|---|
| `pvporcupine` | 4.0.2 | Wake word detection ("Hey Bender") — Picovoice proprietary | **CRITICAL** — free tier sunset 2026-06-30 |
| `pvrecorder` | 1.2.7 | PCM capture for Porcupine wake loop | **CRITICAL** — coupled to pvporcupine |

### STT

| Package | Version | Purpose | Risk |
|---|---|---|---|
| `faster-whisper` | 1.2.1 | CPU STT fallback (CTranslate2 backend) | Medium — CTranslate2 has had breaking releases |
| `webrtcvad-wheels` | 2.0.14 | VAD (30ms frames, aggressiveness 3) — Python 3.13 fork | Medium — single maintainer; no upstream fix path |

### Audio

| Package | Version | Purpose | Risk |
|---|---|---|---|
| `PyAudio` | 0.2.14 | PortAudio bindings for mic input + speaker output | Medium — known instability on ALSA/USB hotplug |
| `numpy` | 2.4.3 | PCM array processing | Low |

### AI

| Package | Version | Purpose | Risk |
|---|---|---|---|
| `anthropic` | 0.84.0 | Claude API client (cloud AI fallback) | Low — actively maintained |
| `hailo_platform` | (system apt, hailo-h10-all v5.1.1) | Hailo NPU inference (LLM, STT, VLM) | **High** — no pip-level version pin; apt upgrade could break HEF format compatibility |

### TTS

| Package | Version | Purpose | Risk |
|---|---|---|---|
| `scipy` | (system or venv) | 22050→44100Hz resampling (`resample_poly`) | Low — but absent from requirements.txt; assumed system package |
| `huggingface_hub` | 1.7.1 | Asset download (Piper model, Whisper HEF) | Low |

### Web

| Package | Version | Purpose | Risk |
|---|---|---|---|
| `fastapi` | 0.115.0 | Web admin API server | Low |
| `uvicorn` | 0.32.0 | ASGI server | Low |
| `python-multipart` | 0.0.20 | Multipart file upload parsing | Low |

### Utilities

| Package | Version | Purpose | Risk |
|---|---|---|---|
| `python-dotenv` | 1.2.2 | `.env` file loading | Low |
| `requests` | 2.32.3 | HTTP client (HA REST API, briefings) | Low |
| `systemd-python` | >=235 | sd_notify watchdog pings | Medium — requires apt `libsystemd-dev`; loose version pin |

### Hardware (system-level, not pip-installable)

| Package | Purpose | Risk |
|---|---|---|
| `lgpio`, `adafruit-blinka`, `neopixel` | GPIO/LED hardware libs | Medium — venv must use `--system-site-packages` |
| `Adafruit-Blinka-Raspberry-Pi5-Neopixel` 1.0.0rc2 | SPI NeoPixel on Pi 5 | Medium — release candidate; no confirmed stable release path |
| `adafruit-circuitpython-neopixel-spi` 1.0.14 | CircuitPython SPI NeoPixel abstraction | Low |

---

## 6. Pain Points & Known Issues

### Wake Word

- **Picovoice free-tier sunset (June 30, 2026).** The API key will stop working. `pvporcupine.create()` will raise an authentication exception on every call. The wake loop crashes continuously; the entire voice pipeline becomes non-functional. Web UI and timers continue independently. See Section 9 for full detail.
- **No visual feedback during wake-word listening.** `led_listening_enabled` defaults to `False`. LEDs give no indication that the system is actively listening. If the mic stalls silently, there is no user-visible sign.
- **USB hot-plug does not recover.** Device indices are cached permanently for the process lifetime. If the ReSpeaker is unplugged and replugged, the cached index may be stale. Service restart required.
- **reSpeaker stereo-to-mono path is divergent.** Porcupine uses left-channel extraction (`[::2]`); STT (`stt.py`) opens the stream with `channels=1`. Whether ALSA or Python handles the downmix for STT is configuration-dependent.

### STT

- **Hailo STT disabled (`hailo_stt_enabled: false`).** Currently using `faster-whisper` on CPU (`tiny.en`). Hailo Whisper-Base HEF reported as not yet stable upstream. STT latency is consequently higher (~1–2s CPU vs expected faster on NPU).
- **Hailo KV-Cache contention.** If the LLM holds the VDevice between turns, STT re-acquisition on the next turn incurs init cost. `release()` tears down the full VDevice; re-init is expensive. Contract enforced by convention only.
- **`release()` VDevice not guaranteed.** `del self._vdevice` decrements Python refcount but the Hailo SDK may hold internal references. Confirmed open issue in HANDOVER.md.

### Intent Classification

- **Keyword/regex only — no ML.** Intent false positives and negatives are a known limitation. The log-driven promotion system partially mitigates this for high-frequency queries but does not address novel phrasings.
- **TIMER_CANCEL label matching is brittle.** If no exact label match and extracted label is not literally "timer"/"alarm", silently returns "I don't see any timer."
- **`ContextualHandler.weather_detail` creates a fresh `AIResponder`.** No conversation history available; separate API connection opened per call.
- **`VisionHandler` blocks synchronously.** Unlike `FutureVisionProvider` (pre-started in background after greeting), `VisionHandler.handle()` calls `vision.analyse_scene()` blocking on the inference thread, stacking on top of `response_hard_timeout_s`.

### AI Routing

- **Thinking sound fires too late.** The thinking clip plays after `get_response()` returns, not during inference. The user hears silence during the longest wait, then the thinking sound immediately followed by the response. UX is degraded.
- **Ollama cold-start latency.** Despite `warm_up()`, Ollama CPU inference can reach the 25s hard timeout on complex queries or under thermal load. No user feedback during this wait.
- **Quality check escalation is opaque.** When local AI fails quality check and escalates to Claude, the user hears no different response — but latency is much higher and a cloud call was made. No logging visible to user.
- **Hailo 60s retry cooldown.** On repeated init failure, the system blocks LLM inference for up to 60s before retrying. Degrades to Ollama or cloud during this window.
- **Scenario classifier has no current effect.** All three scenarios (`conversation`, `knowledge`, `creative`) are mapped to `local_first`. The classifier exists and works but changes nothing in practice.

### TTS

- **Streaming TTS not fully wired end-to-end.** `speak_from_iter()` and `speak_streaming()` exist and are architecturally correct. Whether `responder.py`/`wake_converse.py` actually feeds a streaming sentence iterator into `speak_from_iter` vs calling `speak(full_text)` after LLM completion is unconfirmed. "Streaming LLM responses" remains listed as a Future Consideration.
- **PiperPool config baked at process start.** Changing `speech_rate`, `tts_noise_scale`, or `tts_noise_scale_w` at runtime requires `PiperPool.close()` + re-initialisation. No hot-reload path exists. Config UI writes to JSON file; service restart required.
- **`scipy` not in requirements.txt.** TTS resampling will silently fail on a fresh install without system-level scipy.

### Audio

- **WM8960 single sample rate — missed `close_session()` is fatal.** An uncaught exception in the conversation loop that skips `close_session()` leaves the output stream open at 44100 Hz, blocking the mic on the next wake cycle. There is no recovery path short of a service restart.
- **20ms pre-silence may be insufficient after long gaps.** `SILENCE_PRE` was tuned down from 50ms to 20ms. If the DAC is cold (long silence within a session), 20ms may not prevent a DAC pop on playback start.
- **No barge-in.** Hardware prevents listening while playing — user cannot interrupt Bender mid-sentence.

### Vision

- **VLM disabled (`vlm_enabled: false`).** Hailo KV-Cache contention with LLM and 60s VLM timeout would block the conversation loop. `VISION` intent exists and is wired but produces no useful responses while VLM is off.
- **`VISION` handler falls back to sarcastic "nothing there" lines when VLM is disabled** — passable UX but not informative.
- **Passive vision endpoints not implemented.** `GET/POST/DELETE /api/vision/passive` are wired in the Config UI and `api.js` but absent from `vision.py`. All passive mode controls in Config will 404.
- **IMX500 NPU unused.** The AI Camera's on-chip NPU is not utilised — the camera is used as a plain RGB source.

### System

- **SD card log accumulation.** Conversation `.jsonl` logs and `metrics.jsonl` accumulate on the SD card with no automatic rotation or pruning. Manual cleanup or backup required periodically.
- **Weather briefing returning 401 Unauthorized.** HA token in `.env` likely expired. Weather briefings silently play fallback WAV until token is rotated.
- **No dashboard auto-refresh.** Status data loads once on mount; users must navigate away and back to see updated metrics.
- **"Refresh Briefings" == "Restart Service".** Both buttons issue `sudo systemctl restart bender-converse`. No targeted briefing invalidation exists.
- **Remote page text/audio mismatch.** `POST /api/remote/ask` expects a multipart audio upload; the Remote.svelte page sends JSON text. The endpoint will fail in production as-called from the frontend.
- **Log downloads have no auth.** `GET /api/logs/download/{filename}` has no PIN requirement — anyone on the network can download `bender.log` and `metrics.jsonl`.

---

## 7. Current Mitigations In Place

- **Systemd watchdog + sd_notify heartbeat.** `WatchdogSec=120`; wake loop pings `WATCHDOG=1` every 250 frames. Prevents the 1.5-day silent freeze (prior production incident).
- **Wake loop stall detection.** If no PCM reads for `wake_stall_seconds` (30s), a `RuntimeError("wake loop stalled")` is raised; the outer loop catches it, sleeps 1s, and reinits the Porcupine instance.
- **`response_hard_timeout_s`.** Inference thread is killed after 45s; `error_timeout.wav` is played. Prevents indefinite hang on LLM inference.
- **`http_timeout_s = 10`.** Applied to all `urlopen` calls in briefings and HA fetches. Anthropic client given explicit `httpx.Timeout`.
- **Briefing TTL cache + fallback WAV.** Weather (30min TTL) and news (2hr TTL) pre-generated at startup. On generation failure, a fallback WAV is synthesised and served. Stale/failed briefings are never silent.
- **Hallucination filtering.** Known Whisper phantom phrases, repetitive-character patterns, and overlong outputs filtered from STT results before intent classification.
- **Post-playback reverb flush (200ms).** Mic buffer discarded after playback to prevent speaker echo triggering VAD false positive.
- **Handler registry decoupling.** New intents/handlers can be added without touching the dispatch logic. Handlers return `None` to fall through cleanly.
- **Audio callback decoupling.** `audio.py` has no import of `leds.py`; LED wiring injected as `on_chunk` callback at call site.
- **Piper process pool pre-warming.** `tts_generate.warm_up()` synthesises "test" at startup. Eliminates cold-start on first TTS call.
- **Ollama pre-warming.** `LocalAIResponder.warm_up()` sends a 1-token probe to Ollama at startup to pre-load model weights.
- **STT pre-warming.** `stt.warm_up()` runs `_load_model()` in a background thread at startup to avoid ~5s init on first wake word.
- **Quality check escalation.** Local LLM responses failing quality gate are escalated to Claude rather than serving hedge-phrase or too-short responses to the user.
- **Hailo 60s cooldown retry.** `_HailoLLMResponder._load()` tracks `_last_failed_at`; avoids hammering the NPU init on repeated failure.
- **HA entity cache (60s TTL).** Entity list fetched once per minute rather than per command; reduces HA REST API load.
- **Centralised device discovery.** `find_input_device()` / `find_output_device()` in `audio.py` are the single source of truth for device selection; no longer duplicated across `stt.py` and `wake_converse.py`.
- **ConversationSession extraction.** `wake_converse.py` reduced to ~120 lines (orchestrator only). Session logic isolated for testability.
- **Web app router split.** `web/app.py` decomposed from 967-line flat file into 9 domain routers under `scripts/web/routes/`.
- **Atomic timer persistence.** `timers.json` written via `.tmp` + `os.replace()` to prevent corruption on crash.
- **`SHARED_VDEVICE_GROUP_ID`.** Hailo VDevice opened with shared group ID to allow cooperative sharing between STT and LLM (mitigation for KV-Cache contention; not a complete solution).

---

## 8. Future Considerations Already Identified

Copied from HANDOVER.md (2026-05-25):

- Recurring/repeating timers
- Timer snooze functionality
- Local ML intent classifier (collecting training data via logging)
- Train embedding-based query classifier on HuggingFace once ~500+ labelled routing queries collected (replaces keyword heuristic in `_classify_scenario`)
- Revisit Hailo NPU for LLM if model switching improves — `local_llm_url` can point at hailo-ollama (`localhost:8000`) instead of CPU Ollama (`localhost:11434`)
- Streaming LLM responses — start TTS on first sentence before full response completes
- Adaptive routing thresholds — auto-switch scenario to cloud_only if escalation rate exceeds threshold
- Persistent Piper process (verify --json-input on Pi)
- Split `get_response()` into `classify()` + `generate()` for thinking sounds during generation
- Hailo Whisper-Small HEF when it stabilises on Hailo-10H (currently broken upstream)
- Camera/AI vision via Hailo NPU (future hardware addition)
- Motorised elements for physical Bender model
- Clip categorisation — raw WAVs in `speech/wav/` all land in one "clips" bucket; label under headings (greetings, insults, sound effects, etc.); frontend already groups by category

### Additional items surfaced by subsystem analysis (not yet in HANDOVER.md)

- **OpenWakeWord migration (URGENT — see Section 9).**
- **Passive vision backend implementation.** `GET/POST/DELETE /api/vision/passive` endpoints are fully wired in the Config UI but not implemented in `vision.py`. Requires implementation or UI removal.
- **Remote page audio/text endpoint mismatch.** `POST /api/remote/ask` expects multipart audio; frontend sends JSON text. Either add a text-only endpoint or build a mic-record flow in Remote.svelte.
- **Log download auth gap.** `GET /api/logs/download/{filename}` has no PIN dependency. Should add `require_pin` to match all other authenticated endpoints.
- **Dashboard auto-refresh.** Add periodic polling (e.g. 30s) to the Dashboard status load so metrics, alerts, and session counts update without page navigation.
- **Volume control UI.** `GET/POST /api/config/volume` are implemented and defined in `api.js`. `VolumeSlider` component exists. Surface it in a page (Config or Puppet).
- **LED brightness live endpoint.** Config.svelte currently writes `led_brightness` to `bender_config.json` only; requires restart. Wire Config slider to `POST /api/config/led-brightness` for live updates.
- **Prebuild and generate-status action buttons.** `rebuildResponses()` and `generateStatus()` are defined in `api.js` but called from no page. Add buttons to the Actions or Config section.
- **Timer creation UI.** Timers are fully implemented (CRUD API, store, cards on Dashboard) but can only be created by voice. Add a simple form to the Dashboard or a dedicated Timers page.
- **Observability gaps (see Section 7 of the Vision/Timers/Observability summary):** Missing metrics instrumentation for vision pipeline latency, wake word detection latency, timer alert cycle duration, HA control REST latency, briefing cache hit/miss ratio, session duration, audio device reinit events, TTS cache hit rate, and Hailo NPU temperature/utilisation.
- **`scipy` missing from requirements.txt.** Should be added to prevent silent TTS failure on fresh installs.
- **Hailo SDK version pinning.** `hailo-h10-all` is installed via apt with no pip-level constraint. An `apt upgrade` could silently break HEF format compatibility. Document the locked version and add a setup guard.
- **`webrtcvad-wheels` migration path.** If Python 3.14+ introduces another incompatibility, the community fork has no upstream fix path. Evaluate `silero-vad` (ONNX, well-maintained) as a replacement.
- **Camera docstring specifies IMX708; hardware is IMX500.** Fix the incorrect module docstring in `scripts/camera.py`.
- **IMX500 on-chip NPU.** Currently unused. Evaluate whether the IMX500's neural network processor can offload YOLO/object detection inference, freeing the Hailo NPU for LLM/VLM.

---

## 9. URGENT — Picovoice Free-Tier Sunset (June 30, 2026)

### Deadline

**June 30, 2026** — Picovoice Free Tier disabled. Source: `docs/checkpoints/2026-05-22-picovoice-sunset.md`.

As of the date of this document (June 12, 2026), **18 days remain.**

### Exact packages affected

| Package | Version pinned in requirements.txt |
|---|---|
| `pvporcupine` | `4.0.2` |
| `pvrecorder` | `1.2.7` |

### Every file that references pvporcupine or pvrecorder

| File | What it does |
|---|---|
| `scripts/wake_converse.py` | `import pvporcupine` at line 32; creates instance via `pvporcupine.create(access_key=os.environ["PORCUPINE_ACCESS_KEY"], keyword_paths=["scripts/hey-bender.ppn"])`; uses `porcupine.sample_rate`, `porcupine.frame_length`, `porcupine.process()`, `porcupine.delete()` |
| `scripts/config.py` | Declares `porcupine_access_key: str = ""` field; reads from `PORCUPINE_ACCESS_KEY` env var |
| `.env.example` | Documents `PORCUPINE_ACCESS_KEY=` placeholder |
| `scripts/hey-bender.ppn` | The compiled "Hey Bender" wake word model — gitignored, contains API key material |

`pvrecorder` is a transitive dependency of `pvporcupine`; it has no direct imports in the codebase.

### What will break the moment the API key stops working

`pvporcupine.create(access_key=...)` in `wait_for_wakeword()` will raise an authentication exception on every call. This function is inside the outer `while True` reinit loop in `main()`. The process will loop-crash continuously — logging auth errors on every iteration, never advancing past wake word detection. STT, intent classification, TTS, HA control, briefings, and the AI fallback are all unreachable. **The entire voice pipeline becomes non-functional.** The web UI (`bender-web.service`) and the timer subsystem continue independently, but no voice interaction is possible. There is no graceful degradation path.

### Existing OpenWakeWord migration plan

**Source:** `docs/superpowers/plans/2026-05-22-openwakeword-migration.md`

Replace `pvporcupine` with [openWakeWord](https://github.com/dscripka/openWakeWord) — free, offline, ONNX-based, Apache 2.0 licensed, no API key, no vendor lock-in. The migration scope is narrow: only `wait_for_wakeword()` in `wake_converse.py` needs changing. STT, intent, responder, audio session, and all other components are unaffected.

**Key differences:**

| Aspect | Porcupine | openWakeWord |
|---|---|---|
| Authentication | API key required | None |
| Model format | `.ppn` (proprietary binary) | ONNX / tflite |
| Sample rate | 16000 Hz | 16000 Hz (same) |
| Frame size | ~512 samples (`porcupine.frame_length`) | 1280 samples (80ms) |
| Detection API | Returns int ≥ 0 (wake word index) | Returns score dict `{model_name: float}` |
| Threshold | Binary (match / no match) | Configurable float, ~0.5 typical |
| Custom models | Paid / free tier (sunset) | Open training pipeline (synthetic TTS data) |

**The custom wake word problem:**

"Hey Bender" has no pre-trained openWakeWord model. Two options:

- **Option A — Train a custom model (recommended long-term):** openWakeWord supports training from synthetic TTS-generated audio samples (no real recordings required). ~1–2h compute on a desktop. Outputs a committed ONNX file.
- **Option B — Temporary generic wake word (recommended to unblock before June 30):** Use a pre-trained model (`hey_jarvis`, `alexa`, `hey_mycroft`). Functional immediately; not Bender-branded.

Recommended path: **Option B now to unblock before the deadline, Option A after to restore "Hey Bender".**

**Files to change:**

| File | Change required |
|---|---|
| `requirements.txt` | Remove `pvporcupine==4.0.2`, `pvrecorder==1.2.7`; add `openwakeword` |
| `scripts/wake_converse.py` | Replace Porcupine init/loop block; adjust frame size to 1280 samples; change detection from `>= 0` check to score dict threshold check |
| `scripts/config.py` | Remove `porcupine_access_key`; add `oww_model_path: str`, `oww_threshold: float = 0.5` |
| `.env.example` | Remove `PORCUPINE_ACCESS_KEY` line |
| `CLAUDE.md` | Update env vars table and wake word description |
| `scripts/hey-bender.ppn` | Remove from device (gitignored; delete from `.env`/config reference) |

**Stereo downmix reuse:** The existing left-channel extraction (`pcm[::2]`) for the reSpeaker 4-mic array (currently inside `wait_for_wakeword()`) applies to both Porcupine and openWakeWord since both require mono 16kHz PCM. This code block is directly reusable with the new 1280-sample frame size.

### Open questions not yet resolved by the migration plan

1. **aarch64 / Python 3.13 compatibility not confirmed.** openWakeWord has not been validated on Pi 5 aarch64 with Python 3.13. This must be verified before committing to the migration approach. Failure here would require a different strategy (e.g. Wyoming protocol + a separate wake word service, or Snowboy, or a custom solution).

2. **Custom "Hey Bender" ONNX model does not exist.** Training pipeline not started. Until Option A is executed, "Hey Bender" cannot be used as the wake phrase. The transition period will use a generic wake word.

3. **Threshold tuning required.** The `oww_threshold: 0.5` starting value is an estimate. The home environment includes TV noise and a child — empirical tuning against false positives and false negatives will be needed after deployment.

4. **1280-sample frame size end-to-end verification with reSpeaker stereo path.** The stereo-to-mono downmix uses `[::2]` slicing on the raw buffer. With 1280-sample frames (instead of ~512), the slice size changes. Behaviour needs verification to confirm no off-by-one or buffer-stride issue in the reSpeaker stereo path.

5. **`docs/checkpoints/2026-05-22-picovoice-sunset.md`** — this document is referenced in the migration plan but may contain additional context or decisions not yet reviewed.

6. **Model download and deployment.** The openWakeWord ONNX model file needs to be downloaded and placed on the Pi. A mechanism analogous to the current `hey-bender.ppn` deployment is needed (setup script, git-stored model, or auto-download on first run).
