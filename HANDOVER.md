# BenderPi Handover Context
Last updated: 2026-03-22

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

## Current Priorities
- Test local LLM: verify hybrid routing, quality check escalation, UI config controls
- Test timers: "Hey Bender, set a timer for pasta for 5 minutes"
- Test UI redesign on phone (mobile FAB/bottom sheet)
- Tune scan-line opacity and glow intensity on real device
- Collect Hailo STT metrics baseline and compare against previous faster-whisper CPU baseline
- Monitor STT hallucination rate (Whisper-Base may behave differently to tiny.en)
- Monitor local LLM escalation rates — collect data for future ML classifier training (~500+ queries needed)

## Recent Decisions
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

## Known Issues
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
