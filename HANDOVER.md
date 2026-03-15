# BenderPi Handover Context
Last updated: 2026-03-15

---

## Post-Deploy Steps (run these on the Pi after git pull)

These steps are required after the March 2026 quality overhaul and web UI deploy. Run them once, in order, via SSH (`ssh pi@192.168.68.132`).

### Step 1: Install new Python dependencies

```bash
cd /home/pi/bender
venv/bin/pip install fastapi==0.115.0 uvicorn==0.32.0
```

### Step 2: Pre-generate thinking sounds

```bash
venv/bin/python scripts/prebuild_responses.py
```

This generates `speech/responses/thinking/thinking_*.wav` files and updates `index.json`. These are played while Bender prepares dynamic responses.

### Step 3: Set web UI PIN in .env

```bash
nano /home/pi/bender/.env
```

Add (choose your own PIN — do NOT use the default):
```
BENDER_WEB_PIN=<your-pin-here>
BENDER_WEB_PORT=8080
```

### Step 4: Install and enable the web UI service

```bash
sudo cp /home/pi/bender/systemd/bender-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bender-web
sudo systemctl start bender-web
```

Verify it's running:
```bash
sudo systemctl status bender-web
curl -s http://localhost:8080/api/health
```

### Step 5: Set up sudoers for service control

The web UI needs passwordless sudo to restart/stop/start the conversation service:

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

### Step 6: Restart the conversation service

The quality overhaul changes (structured logging, metrics, responder extraction, intent hardening, etc.) require a service restart to take effect:

```bash
sudo systemctl restart bender-converse
```

### Step 7: Verify everything works

```bash
# Check both services are running
sudo systemctl status bender-converse
sudo systemctl status bender-web

# Check web UI is accessible
curl -s http://localhost:8080/api/health

# Check logs are being written
ls -la /home/pi/bender/logs/

# Open web UI in browser
# http://192.168.68.132:8080
```

### Step 8: Wire speech_rate into TTS (optional, quick)

The `speech_rate` config field exists in `bender_config.json` but isn't wired into Piper yet. To enable it, edit `scripts/tts_generate.py` and add `--length-scale` to the Piper command:

```python
# In the speak() function, where the Piper subprocess is called, add:
# "--length-scale", str(cfg.speech_rate),
# to the command list, after "--output_file"
```

Then restart the service. Values: 1.0 = normal, 1.2 = slower, 0.8 = faster.

---

## Current Priorities
- Collect metrics baseline data (first week after deployment)
- Monitor STT hallucination rate to decide if Whisper model upgrade is needed
- Watch intent multi-match warnings to identify patterns needing further tightening
- Test web UI on phone and desktop, tune responsive layout if needed

## Recent Decisions
- Chose interleaved approach: foundation (logging/metrics) first, then modularity, then improvements
- Separated execute() from control() in ha_control.py for web UI readiness
- Extracted response chain into responder.py — wake_converse.py is now a thin orchestrator
- Chose not to purchase AI HAT+ for now — software improvements offer better ROI (see docs/ai-hat-plus-analysis.md)
- Thinking sounds play after response generation (not during) — architectural limitation to address when adding async generation
- Web UI built with FastAPI + vanilla HTML/CSS/JS (no framework, no build step)
- Puppet-only mode toggle stops/starts bender-converse service
- Volume control via amixer, speech rate via config (Piper --length-scale)

## Known Issues
- Piper --json-input mode needs verification on the Pi (persistent subprocess not yet implemented — using warm-up fallback)
- Intent false positives reduced but not eliminated — utterance-length heuristic may need tuning
- Thinking sound timing: plays after get_response() returns, not during generation
- speech_rate config field exists but is not yet wired into tts_generate.py (see Step 8 above)

## Future Considerations
- Local ML intent classifier (collecting training data via improved logging)
- Whisper model upgrade to base.en or distil-whisper (needs metrics baseline first)
- Local LLM via llama.cpp to reduce API dependency
- Persistent Piper process (verify --json-input on Pi first)
- AI HAT+ if camera/vision features are added (see docs/ai-hat-plus-analysis.md)
- Split get_response() into classify() + generate() to enable thinking sounds during generation
- Add LED GPIO pin config to bender_config.json if hardware changes
