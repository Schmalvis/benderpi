# BenderPi Handover Context
Last updated: 2026-03-15

## Current Priorities
- Collect metrics baseline data (first week after deployment of observability changes)
- Monitor STT hallucination rate to decide if Whisper model upgrade is needed
- Watch intent multi-match warnings to identify patterns needing further tightening
- Run prebuild_responses.py on the Pi to generate thinking sound WAVs

## Recent Decisions
- Chose interleaved approach: foundation (logging/metrics) first, then modularity, then improvements
- Separated execute() from control() in ha_control.py for future web UI
- Extracted response chain into responder.py — wake_converse.py is now a thin orchestrator
- Chose not to purchase AI HAT+ for now — software improvements offer better ROI (see docs/ai-hat-plus-analysis.md)
- Thinking sounds play after response generation (not during) — architectural limitation to address when adding async generation

## Known Issues
- Piper --json-input mode needs verification on the Pi (persistent subprocess not yet implemented — using warm-up fallback)
- Thinking sounds need pre-generating on the Pi: run `venv/bin/python scripts/prebuild_responses.py`
- Intent false positives reduced but not eliminated — utterance-length heuristic may need tuning
- Thinking sound timing: plays after get_response() returns, not during generation (reduces its effectiveness as a "patience" signal)

## Future Considerations
- Web UI for log viewing, config adjustment, and puppet mode (architecture now supports it via play_oneshot, execute(), get_*_text())
- Local ML intent classifier (collecting training data via improved logging)
- Whisper model upgrade to base.en or distil-whisper (needs metrics baseline first)
- Local LLM via llama.cpp to reduce API dependency
- Persistent Piper process (verify --json-input on Pi first)
- AI HAT+ if camera/vision features are added (see docs/ai-hat-plus-analysis.md)
- Split get_response() into classify() + generate() to enable thinking sounds during generation
