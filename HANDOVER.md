# BenderPi Handover Context
Last updated: 2026-03-21

---

## Post-Deploy Steps (run on Pi after git pull)

### New dependencies
```bash
cd /home/pi/bender
venv/bin/pip install fastapi==0.115.0 uvicorn==0.32.0
```

### Generate thinking sounds
```bash
venv/bin/python scripts/prebuild_responses.py
```

### Set web UI PIN
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

### Set up sudoers
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

### Restart conversation service
```bash
sudo systemctl restart bender-converse
```

### Wire speech_rate into TTS
Edit `scripts/tts_generate.py` — add `"--length-scale", str(cfg.speech_rate)` to the Piper command. Restart service.

---

## Current Priorities
- Run `venv/bin/python scripts/prebuild_responses.py` on Pi to generate timer alert + thinking clips
- Test timers: "Hey Bender, set a timer for pasta for 5 minutes"
- Test UI redesign on phone (mobile FAB/bottom sheet)
- Tune scan-line opacity and glow intensity on real device
- Collect metrics baseline (first week)
- Monitor STT hallucination rate

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
- Speech rate via config (Piper --length-scale)
- Chose not to purchase AI HAT+ — software improvements offer better ROI

## Known Issues
- Piper --json-input mode needs verification on Pi (using warm-up fallback)
- Intent false positives reduced but not eliminated
- Thinking sound timing: plays after get_response() returns, not during generation
- speech_rate config exists but not yet wired into tts_generate.py
- Timer alert clips need pre-generating on Pi via prebuild_responses.py

## Future Considerations
- Recurring/repeating timers
- Timer snooze functionality
- Local ML intent classifier (collecting training data via logging)
- Whisper model upgrade to base.en or distil-whisper (needs metrics baseline)
- Local LLM via llama.cpp to reduce API dependency
- Persistent Piper process (verify --json-input on Pi)
- AI HAT+ if camera/vision features added (see docs/ai-hat-plus-analysis.md)
- Split get_response() into classify() + generate() for thinking sounds during generation
