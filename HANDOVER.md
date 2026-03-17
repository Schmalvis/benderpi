# BenderPi Handover Context
Last updated: 2026-03-17

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
- Test UI redesign on phone (mobile FAB/bottom sheet)
- Tune scan-line opacity and glow intensity on real device
- Collect metrics baseline (first week)
- Monitor STT hallucination rate
- Watch intent multi-match warnings

## Recent Decisions
- UI redesigned with Futurama Theme C (scan lines, glows, gradient cards, animated status)
- Added persistent sidebar with quick controls (volume, LED, puppet-only, silent wake, end session)
- End-session via file-based IPC (.end_session / .session_active.json)
- LED listening/talking colours: blue when listening, white when talking (configurable)
- Silent wake word mode: LED-only notification, no audio greeting
- Web UI built with FastAPI + vanilla HTML/CSS/JS (no framework)
- Puppet-only mode toggle stops/starts bender-converse service
- Speech rate via config (Piper --length-scale)
- Chose not to purchase AI HAT+ — software improvements offer better ROI

## Known Issues
- Piper --json-input mode needs verification on Pi (using warm-up fallback)
- Intent false positives reduced but not eliminated
- Thinking sound timing: plays after get_response() returns, not during generation
- speech_rate config exists but not yet wired into tts_generate.py

## Future Considerations
- Local ML intent classifier (collecting training data via logging)
- Whisper model upgrade to base.en or distil-whisper (needs metrics baseline)
- Local LLM via llama.cpp to reduce API dependency
- Persistent Piper process (verify --json-input on Pi)
- AI HAT+ if camera/vision features added (see docs/ai-hat-plus-analysis.md)
- Split get_response() into classify() + generate() for thinking sounds during generation
