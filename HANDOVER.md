# BenderPi Handover Context
Last updated: 2026-03-18

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

### Hailo AI HAT+ 2 setup (already done — notes for re-provisioning)
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
- Run `venv/bin/python scripts/prebuild_responses.py` on Pi to generate timer alert + thinking clips
- Test timers: "Hey Bender, set a timer for pasta for 5 minutes"
- Test UI redesign on phone (mobile FAB/bottom sheet)
- Tune scan-line opacity and glow intensity on real device
- Collect Hailo STT metrics baseline and compare against previous faster-whisper CPU baseline
- Monitor STT hallucination rate (Whisper-Base may behave differently to tiny.en)

## Recent Decisions
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
- **Raspberry Pi AI HAT+ 2 (Hailo-10H, 40 TOPS) installed 2026-03-18** — STT now runs Whisper-Base on NPU via `hailo_platform.genai.Speech2Text`. CPU faster-whisper fallback retained in `stt.py` if HEF unavailable. Metric label changed from `tiny.en` to `whisper-base-hailo`.

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
- Local LLM via hailo-ollama to reduce/eliminate Claude API dependency (Hailo-10H capable)
- Persistent Piper process (verify --json-input on Pi)
- Split get_response() into classify() + generate() for thinking sounds during generation
- Hailo Whisper-Small HEF when it stabilises on Hailo-10H (currently broken upstream)
