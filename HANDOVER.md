# BenderPi Handover Context
Last updated: 2026-05-14

---

## Post-Deploy Steps (run on Pi after git pull)

### Install/update dependencies
```bash
cd /home/pi/bender
venv/bin/pip install -r requirements.txt
```

### Generate pre-built response clips
Run this after any changes to response text or on a fresh deploy:
```bash
venv/bin/python scripts/prebuild_responses.py
```

### Restart conversation service
```bash
sudo systemctl restart bender-converse
```

---

## One-Time Setup (already done on this Pi — notes for re-provisioning)

### Web UI PIN + port
```bash
nano /home/pi/bender/.env
# Add: BENDER_WEB_PIN=<your-pin>  BENDER_WEB_PORT=8080
```

### Install web UI service
```bash
sudo cp systemd/bender-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bender-web
sudo systemctl start bender-web
```

### Set up sudoers for web UI
```bash
sudo tee /etc/sudoers.d/bender-web << 'EOF'
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl stop bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl start bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart bender-web
pi ALL=(ALL) NOPASSWD: /bin/systemctl status bender-converse
EOF
sudo chmod 440 /etc/sudoers.d/bender-web
```

### Install Ollama (local LLM)
Already installed. To restore on a fresh Pi:
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b
sudo systemctl enable ollama
sudo systemctl start ollama
# Verify: ollama list should show qwen2.5:1.5b
```

### Hailo AI HAT+ 2 setup
The Hailo-10H is installed and `hailo-h10-all` v5.1.1 is present. The Whisper-Base HEF lives at
`/usr/local/hailo/resources/models/hailo10h/Whisper-Base.hef`. To restore it on a fresh Pi:
```bash
# Install hailo packages (via apt — already configured)
sudo apt install hailo-h10-all

# Install hailo-apps into bender venv (for the download tool)
git clone https://github.com/hailo-ai/hailo-apps.git ~/hailo-apps
sudo mkdir -p /usr/local/hailo/resources && sudo chown -R pi:pi /usr/local/hailo
cd ~/hailo-apps && /home/pi/bender/venv/bin/pip install -e . --quiet

# Download Whisper-Base HEF (~137MB)
/home/pi/bender/venv/bin/hailo-download-resources --group whisper_chat --arch hailo10h

sudo systemctl restart bender-converse
```

---

## 2026-05-14 — Audio resilience + decoupling (complete)

Implemented `docs/superpowers/plans/2026-05-14-audio-resilience.md` (12 tasks). All committed to main.

**What changed:**
- Central device discovery (`audio.find_input_device` / `find_output_device`) — no more hardcoded ALSA indices
- Wake-loop stall detection (30s zero-read → reinit) + sd_notify watchdog (`WatchdogSec=120`)
- `watchdog.check_session_liveness` — alerts if no conversation in 6h (would have caught the 1.5-day freeze)
- Lazy vision injection — STT starts immediately after greeting; VLM result injected when the AI call fires
- `response_hard_timeout_s=20` — inference thread bounded; plays `error_timeout.wav` on breach
- HTTP timeouts on briefings + Anthropic client (`http_timeout_s=10`, httpx.Timeout)
- 18 magic constants migrated to `bender_config.json`; `WHISPER_HALLUCINATIONS` to JSON

**Pi deploy required** (one-time steps for this plan):
```bash
cd /home/pi/bender && git pull origin main
sudo apt-get install -y libsystemd-dev pkg-config
venv/bin/pip install -r requirements.txt
venv/bin/python scripts/prebuild_responses.py  # generates error_timeout.wav
sudo cp systemd/bender-converse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart bender-converse
```
Verify: `systemctl show bender-converse -p WatchdogUSec,Type,NotifyAccess` → `WatchdogUSec=2min`, `Type=notify`, `NotifyAccess=main`.

---

## 2026-05-14 — vlm.py Qwen2-VL-2B rewrite + reSpeaker stereo + web fixes (complete)

Deployed alongside the audio-resilience plan.

**What changed:**
- `vlm.py`: replaced YOLO+LLM two-step pipeline with Qwen2-VL-2B direct VLM
  (`hailo_platform.genai.VLM`, 336×336 RGB input, single HEF)
- `audio.py`: added `xvf_dsnoop` to `find_input_device` fallback hint chain
- `wake_converse.py`: xvf_dsnoop stereo detection + left-channel downmix for Porcupine
- `web/app.py`: `_check_camera()` TTL cache (10s); `vision_analyse` stops/starts
  `bender-converse` around TTS to prevent mic-bleed feedback loop

**Pi deploy status (2026-05-14 16:42):** deployed, service running cleanly.
Verified: `WatchdogUSec=2min`, `Type=notify`, `READY=1 sent`, mic=`mic_shared idx=11 ch=1`.

---

## 2026-05-24/25 — Architectural deepening (complete)

All 4 refactor candidates identified in the architecture review are done and deployed.

**1. `ha_control.py` decomposition** — 440-line monolith split into 4 focused modules:
- `handlers/entity_registry.py` — entity discovery + TTL cache (60s)
- `handlers/entity_matcher.py` — pure fuzzy matching, two-phase scoring
- `handlers/ha_client.py` — injectable HAClient Protocol + UrllibHAClient
- `handlers/ha_control.py` — thin orchestrator; `make_default(cfg)` factory
- `handlers/ha_handler.py` — pronoun state (`_last_entities`) moved to handler instance

**2. `wake_converse.py` → ConversationSession** — new `scripts/session.py`:
- `ConversationSession` owns: audio open/close, greeting, turn dispatch, vision injection, logging, IPC
- `VisionProvider` Protocol + `FutureVisionProvider` (ThreadPoolExecutor)
- `TurnResult` dataclass with `should_end` + `end_reason`
- `wake_converse.py` reduced to wake word detection + STT loop (~120 lines vs ~500 before)

**3. `briefings.py` — source abstraction + new capabilities**:
- `_get_briefing_wav(key, ttl, wav_path, generate_text, fallback_text)` — core pattern extracted
- `BriefingSource` dataclass + `_SOURCES` list drives `refresh_all()` as a loop
- `get_weather_wav_for_location(location)` — now cached (was ephemeral temp file)
- New: `get_time_wav(timezone)` / `get_time_text(timezone)` — 60s TTL, pure zoneinfo

**4. `web/app.py` → domain routers** — 967-line flat file split into 9 modules:
- `routes/health.py`, `actions.py`, `config.py`, `puppet.py`, `logs.py`, `timers.py`, `status.py`, `remote.py`, `vision.py`
- `app.py` is now 32 lines; auth applied at router level

All deployed and verified clean on BenderPi (2026-05-25).

---

## Current Priorities
- Monitor Ollama escalation rates
- Monitor Hailo LLM KV-Cache on restart — retry cooldown should clear it within 60s if it occurs
- Test timers: "Hey Bender, set a timer for pasta for 5 minutes"
- Monitor local LLM escalation rates — collect data for future ML classifier training (~500+ queries needed)
- **Wire up new briefing capabilities** — `get_time_wav()` and `get_weather_wav_for_location()` exist but aren't yet connected to intent routing in `responder.py`

## Recent Decisions
- **Diagnosis fixes (2026-05-09):** bender-converse was inactive for 3.5 weeks. Three bugs fixed:
  - `local_llm_timeout` raised 3 → 25s; `LocalAIResponder.warm_up()` pre-loads Ollama in background thread at startup (`scripts/ai_local.py`, `scripts/wake_converse.py`)
  - Hailo LLM `_load()` now retries after 60s cooldown instead of permanently caching failure; `close()` + atexit handler releases VDevice/KV-Cache on SIGTERM (`scripts/ai_local.py`)
  - Camera-busy `RuntimeError` in `analyse_scene()` now logs at WARNING (not ERROR+traceback) (`scripts/vision.py`)
  - Service verified running: `Ollama model pre-loaded`, `Listening for 'Hey Bender'` confirmed in logs
- Architecture refactor (2026-03-21): handler registry replaces if/elif dispatch in responder.py
  - New handlers: `handlers/clip_handler.py`, `pregen_handler.py`, `promoted_handler.py`, `weather_handler.py`, `news_handler.py`, `ha_handler.py`, `timer_alert.py`
  - To add a new handler: create class extending `Handler` from `handler_base.py`, declare `intents`, implement `handle()`, add to handler list in `Responder.__init__`
  - `audio.py` decoupled from `leds` — uses `on_chunk`/`on_done` callbacks
  - Config unified: stt.py, tts_generate.py, ha_control.py, briefings.py all use `cfg` singleton
  - IPC paths (`session_file`, `end_session_file`) centralised in config.py
- Added voice-controlled timers and alarms with named labels and concurrent support
- Timer alerts use play-pause cycle (play clip → listen for dismissal → repeat) respecting WM8960 constraint
- Timer persistence via timers.json (best-effort, survives restarts)
- Duration parser supports natural language: "ten minutes", "half an hour", "an hour and a half"
- UI redesigned with Futurama Theme C (scan lines, glows, gradient cards, animated status)
- Added persistent sidebar with quick controls (volume, LED, puppet-only, silent wake, end session)
- End-session via file-based IPC (.end_session / .session_active.json)
- LED listening/talking colours: blue when listening, white when talking (configurable)
- Silent wake word mode: LED-only notification, no audio greeting
- Speech rate via config (Piper --length-scale), wired into tts_generate.py
- **Raspberry Pi AI HAT+ 2 (Hailo-10H, 40 TOPS) installed 2026-03-18** — STT now runs Whisper-Base on NPU via `hailo_platform.genai.Speech2Text`. CPU faster-whisper fallback retained in `stt.py` if HEF unavailable. Metric label changed from `tiny.en` to `whisper-base-hailo`.
- **Local LLM via Ollama (2026-03-21, deployed 2026-03-22):** Qwen2.5-1.5B on CPU, hybrid routing with quality-check escalation to Claude
  - STT stays on Hailo NPU, LLM runs on CPU via Ollama (model switching penalty makes NPU impractical for both)
  - Scenario-based routing: conversation/knowledge/creative, each configurable via web UI
  - Local-first: try local LLM, escalate to Claude on quality failure (hedge phrases, too short, timeout)
  - Rich logging captures routing decisions in `ai_routing` field of conversation log for future ML classifier training
  - Config keys: `ai_backend`, `local_llm_model`, `local_llm_url`, `local_llm_timeout`, `ai_routing`
  - New file: `scripts/ai_local.py` — `LocalAIResponder` with `QualityCheckFailed` exception
- **Svelte + Tailwind UI migration (2026-03-22):** Vanilla JS frontend replaced with Svelte 4 + Tailwind CSS
  - Built locally with Vite, output committed to `web/dist/` — no Node.js on Pi
  - FastAPI serves `web/dist/` instead of `scripts/web/static/`
  - Futurama theme preserved via CSS custom properties wrapped as Tailwind utilities
  - 5 pages: Dashboard, Puppet, Config, Logs, Remote
  - Centralised API client (`web/src/lib/api.js`), Svelte stores for shared state
  - Mobile responsive: bottom tab bar on narrow screens
  - Bundle: ~75 KB JS + ~18 KB CSS (gzipped: ~27 KB total)

## Known Issues
- **Weather briefing 401 Unauthorized** — HA token likely expired. Check `HA_TOKEN` in `/home/pi/bender/.env`.
- **Hailo LLM KV-Cache conflict** — if KV-Cache is still locked after restart (e.g. from bender-web YOLO pipeline holding Hailo), bender-converse retries every 60s. Should self-heal; if not, `sudo systemctl restart bender-web` to release.
- **`del self._vdevice` may not fully release Hailo VDevice** — `del` decrements Python refcount but Hailo SDK may hold internal refs. If KV-Cache lock recurs, check Hailo SDK for an explicit `release()`/`close()` API.
- **Camera-busy check uses substring match** — `"busy" in str(e).lower()` works for today's libcamera EBUSY message. If future SDK versions change the wording it silently regresses. Deferred `CameraBusyError` typed exception in `scripts/camera.py` would fix this cleanly.
- **ReSpeaker XVF3800 4-Mic Array** — now primary mic. Device discovery is by name (`mic_shared`/`seeed`), not hardcoded index — USB hotplug no longer breaks audio.
- Piper --json-input mode needs verification on Pi (using warm-up fallback)
- Intent false positives reduced but not eliminated
- Thinking sound timing: plays after get_response() returns, not during generation
- Local LLM quality depends on Qwen2.5-1.5B — expect escalation for factual/knowledge queries
- Log retention: conversation logs on SD card accumulate routing data for future classifier training — back up periodically

## Future Considerations
- Recurring/repeating timers
- Timer snooze functionality
- Local ML intent classifier (collecting training data via logging)
- Train embedding-based query classifier on HuggingFace once ~500+ labelled routing queries collected (replaces keyword heuristic in `_classify_scenario`)
- Revisit Hailo NPU for LLM if model switching improves — `local_llm_url` can point at hailo-ollama (`localhost:8000`) instead of CPU Ollama (`localhost:11434`)
- Streaming LLM responses — start TTS on first sentence before full response completes
- Adaptive routing thresholds — auto-switch scenario to cloud_only if escalation rate exceeds threshold
- Persistent Piper process (verify --json-input on Pi)
- Split get_response() into classify() + generate() for thinking sounds during generation
- Hailo Whisper-Small HEF when it stabilises on Hailo-10H (currently broken upstream)
- Camera/AI vision via Hailo NPU (future hardware addition)
- Motorised elements for physical Bender model
- **Clip categorisation** — raw WAVs in speech/wav/ all land in one "clips" bucket. Categorise under headings (greetings, insults, sound effects, etc.). Data task: label clips in index.json, frontend already groups by category.
