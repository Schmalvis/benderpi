# BenderPi — Architectural Review & Improvement Plan

**Reviewer:** Fable 5 (Claude) — comprehensive architectural review
**Date:** 2026-06-12
**Source:** `docs/benderpi-review-brief.md` (read in full)

---

## Executive Summary

- **Picovoice free-tier sunset in 18 days (June 30, 2026) will loop-crash the entire voice pipeline; migrate to openWakeWord with a generic wake word immediately, then train a custom "Hey Bender" ONNX model** (Section C).
- **The thinking sound fires after inference completes instead of during it, and streaming LLM→TTS is architecturally complete but unconfirmed end-to-end — together these are the two largest perceived-latency wins available today** (A.1, A.2).
- **A missed `audio.close_session()` on any exception path permanently bricks wake-word detection until service restart; this needs a `try/finally` + context-manager guarantee plus a recovery path in the outer loop** (A.4).
- **Quality-check escalations to Claude are invisible to the operator — no metric, no dashboard surface — so the "offline-first" promise cannot be verified or tuned** (A.6).
- **The mic is now a USB ReSpeaker XVF3800, not the WM8960 — the documented "single sample rate" constraint that forbids barge-in and forces session open/close choreography may no longer apply and should be empirically retested** (Section D, B.5).

---

## Section A: Near-Term Improvements

Ordered by impact/effort ratio.

### A.1 — Play thinking sound DURING AI generation, not after

**Problem:** `session.py:handle_turn()` launches the inference thread, joins with a 150ms timeout, and only then plays the thinking clip — but per the brief (Section 6, AI Routing), the clip actually plays *after* `get_response()` returns. The user sits in silence for 3–25s (Hailo/Ollama), then hears a thinking sound immediately followed by the answer. The cue is worse than useless: it adds latency at the end.

**Solution:** The HANDOVER item "split `get_response()` into `classify()` + `generate()`" is the right shape. Concretely:
1. `Responder.classify(text)` runs intent classification and handler lookup synchronously (<1ms) and returns a `RoutePlan` indicating whether the route is instant (static WAV / cache hit) or slow (AI, briefing miss, HA REST).
2. For slow routes, `session.py` plays the thinking clip *immediately* (the output stream is already open — no sample-rate conflict), then runs `generate()` on the inference thread.
3. Optionally loop a short ambient "processing" clip with abort-on-ready, checked per-chunk via the existing `on_chunk`/abort-flag machinery in `audio.play()`.

**Files affected:** `scripts/responder.py`, `scripts/session.py`, `scripts/handlers/*` (handlers expose an `is_instant` hint or `classify` returns route metadata).
**Expected impact:** Eliminates the perceived dead-air window on every AI-routed turn (3–25s today); thinking sound becomes a genuine "I heard you" acknowledgement within ~200ms of end-of-speech.
**Effort:** Medium

### A.2 — Wire streaming LLM → TTS end-to-end and verify on-device

**Problem:** The plumbing exists — `ai_response.py:respond_streaming()` yields sentences, `tts_generate.speak_from_iter()` feeds the PiperPool, `audio.play_stream()` exists — but the brief (Section 6, TTS) states it is *unconfirmed* whether `responder.py`/`session.py` actually feed a sentence iterator into `speak_from_iter`, or whether they wait for the full LLM response and call `speak(full_text)`. "Streaming LLM responses" is still listed as a Future Consideration in HANDOVER.md, which strongly suggests the non-streaming path is live.

**Solution:**
1. Audit `responder.py:_respond_ai()` — make `ResponseStream` the return type for all three AI tiers, not just Claude. For `_HailoLLMResponder` and `_OllamaResponder`, expose token streams (Ollama's `/api/chat` supports `"stream": true`; check `hailo_platform.genai` for a token callback — if absent, fall back to full-text for the Hailo tier only).
2. Implement a sentence-boundary accumulator (split on `[.!?]` + minimum length) that yields sentences from the token stream into `speak_from_iter()`.
3. **Quality-check interaction:** `check_response_quality()` currently runs on the complete local response before escalation. For streaming local tiers, run the quality gate on the *first sentence only* (hedge phrases appear at the start: "I'm not sure", "As an AI…") plus a minimum-length check — escalate before any audio plays. Document the residual risk that a response degrades after sentence one.
4. Add a metrics timer: `time_to_first_audio_s` per AI turn.

**Files affected:** `scripts/responder.py`, `scripts/ai_local.py`, `scripts/ai_response.py`, `scripts/session.py`, `scripts/metrics.py`.
**Expected impact:** Claude-tier responses already stream; extending to Ollama cuts time-to-first-audio on the CPU tier from 5–25s (full completion + TTS) to roughly first-sentence-generation + ~1–3s Piper synthesis. Combined with A.1 the longest silent waits in the system disappear.
**Effort:** Medium

### A.3 — Persistent Piper: verify on-Pi, add hot-reload, close the HANDOVER item

**Problem:** This item is *already substantially done* — `PiperPool` maintains 3 persistent `--json-input` subprocesses with auto-restart and pre-warming (brief Section 3). Two gaps remain: (a) HANDOVER.md still lists "Persistent Piper process (verify --json-input on Pi)" as future work, implying production verification was never recorded; (b) `speech_rate` / `tts_noise_scale` / `tts_noise_scale_w` are baked at process start — the Config UI writes JSON but a service restart is required, which is a silent footgun for operators.

**Solution:**
1. Verify and record: confirm `metrics.jsonl` shows no per-call spawn latency and that worker auto-restart fires on a killed Piper process; update HANDOVER.md.
2. Add `PiperPool.reload(cfg)` — drain the queue, `close()`, re-init with new params. Expose via `POST /api/config/tts-reload` (PIN-gated) and call it from the Config save path when TTS keys change.
3. Add a pool health metric (worker restarts counter) to `metrics.py`.

**Files affected:** `scripts/tts_generate.py`, `scripts/web/routes/config.py`, `web/src/pages/Config.svelte`, `HANDOVER.md`.
**Expected impact:** Removes the ~300ms+ per-call spawn (already achieved — now verified and documented); TTS tuning becomes live instead of requiring a restart of the whole voice service mid-day.
**Effort:** Low

### A.4 — Make `close_session()` un-skippable; add audio-device recovery path

**Problem:** Brief Hard Constraint 1: a missed `audio.close_session()` on any exception path locks the WM8960 output at 44100 Hz and breaks wake-word detection on the next cycle, with *no recovery short of a service restart*. The session loop has many exception sources (STT, handlers, HA REST, Hailo init, TTS).

**Solution:**
1. Make `ConversationSession` a context manager (`__enter__`/`__exit__`) or wrap the entire session body in `try/finally: audio.close_session()`. `close_session()` itself must be idempotent and exception-safe (swallow `OSError` on an already-dead stream).
2. In the outer `wake_converse.py` reinit loop, on `RuntimeError("wake loop stalled")` or mic-open failure, attempt `audio.force_reset()` — close any lingering output stream, terminate and recreate the shared PyAudio instance — before reinit. This also partially addresses the USB hot-plug staleness (device indices re-enumerated on reset).
3. Emit a `audio_session_leak` counter metric when `close_session()` is reached via the finally path after an exception.

**Files affected:** `scripts/session.py`, `scripts/audio.py`, `scripts/wake_converse.py`, `scripts/metrics.py`.
**Expected impact:** Eliminates the single most severe known failure mode (silent total loss of voice input requiring manual restart); converts USB hot-plug from "restart required" to "auto-recovers within one stall-detection window (30s)".
**Effort:** Low–Medium

### A.5 — Wake word ghost-trigger reduction (engine-agnostic)

**Problem:** False wakes in a home with TV noise and a child. Current mitigations (reverb flush, hallucination filtering) act *after* wake; nothing reduces spurious detections at the wake stage itself, and there is no instrumentation to even measure the false-wake rate. These improvements apply to both Porcupine today and openWakeWord post-migration.

**Solution:**
1. **VAD pre-gate:** run webrtcvad (already a dependency) on wake-loop frames; only feed the wake engine when speech is present. Cuts detections triggered by music/TV broadband energy and reduces idle CPU.
2. **Consecutive-frame confirmation (OWW):** require the score to exceed threshold on N≥2 consecutive 80ms frames before triggering — the single cheapest false-positive reduction for score-based engines. [UNVERIFIED — needs tuning on-device]
3. **Post-wake verification:** after wake, the first STT result is already produced — log a `wake_followed_by_empty_stt` counter. A wake followed by an empty/hallucination-filtered transcript within the first turn is almost certainly a ghost trigger; this gives a measurable false-wake proxy with zero new hardware.
4. **LED listening feedback:** enable a low-brightness `led_listening_enabled` idle pattern so ghost wakes are *visible* (currently `False`; the user can't tell a ghost wake from a mic stall).
5. **TV-hours threshold profile:** optionally raise the detection threshold during configured hours (evening TV) via `bender_config.json`.

**Files affected:** `scripts/wake_converse.py`, `scripts/config.py`, `scripts/metrics.py`, `scripts/leds.py`, `bender_config.json`.
**Expected impact:** Measurable false-wake rate for the first time (counter + STATUS.md surface); consecutive-frame + VAD gating typically cuts false positives substantially [UNVERIFIED — needs on-device A/B over ~1 week of logs].
**Effort:** Medium

### A.6 — Quality-check escalation visibility (operator observability)

**Problem:** When local AI fails `check_response_quality()` and escalates to Claude, nothing is visible: no metric, no dashboard panel, no per-turn annotation. The operator cannot answer "what fraction of AI turns hit the cloud?" — which is the *core KPI* of an offline-first system — nor see *why* (hedge phrase vs too-short vs Hailo cooldown vs Ollama timeout).

**Solution:**
1. In `responder.py:_respond_ai()`, emit a structured metric per AI turn: `{tier_attempted, tier_served, escalation_reason, local_latency_s, cloud_latency_s}`. `QualityCheckFailed.reason` already carries the why — log it.
2. Add the method/reason to the conversation `.jsonl` turn record so `review_log.py` can break it down.
3. Surface on the Dashboard: "AI turns (7d): N local / M escalated (X%) — top reasons: …". Add a watchdog threshold (`max_escalation_rate`) to `watchdog_config.json` so STATUS.md alerts when the local stack degrades.

**Files affected:** `scripts/responder.py`, `scripts/ai_local.py`, `scripts/metrics.py`, `scripts/conversation_log.py`, `scripts/review_log.py`, `scripts/watchdog.py`, `scripts/web/routes/status.py`, `web/src/pages/Dashboard.svelte`.
**Expected impact:** Escalation rate becomes a first-class measured quantity; this is also the prerequisite data for B.3 (adaptive routing) and directly feeds the ML classifier training set (B.1).
**Effort:** Low–Medium

### A.7 — Intent classifier brittleness: confidence, overlap tests, and TIMER_CANCEL fix

**Problem:** `intent.py` is ordered regex/keyword with hard dispatch precedence (`HA_CONTROL` → `TIMER*` → … → `UNKNOWN`). Known failure modes from the brief: TIMER_CANCEL silently returns "I don't see any timer" when label extraction misses; `GREETING`/`AFFIRMATION` require a word-count guard to avoid swallowing commands; ambiguous overlaps ("turn off the timer" — HA_CONTROL or TIMER_CANCEL?) are resolved purely by list order with no test coverage asserting the order.

**Solution (pre-ML hardening — B.1 is the real fix):**
1. **TIMER_CANCEL:** on no exact label match, fuzzy-match against active timer labels (reuse `entity_matcher.py`'s normalisation/fuzzy logic); if exactly one timer is active, cancel it regardless of label; if ambiguous, ask ("Which one — pizza or laundry?") instead of the dead-end response.
2. **Golden test corpus:** create `tests/test_intent_corpus.py` with ~100 utterance→intent pairs sourced from real `logs/*.jsonl` transcripts (they exist and are real user phrasings). Every future pattern change runs against it. This is the single highest-leverage guard against regex regressions.
3. **Return `(intent, confidence)`** where confidence is `1.0` for exact keyword hits and lower for loose regex matches; log it per turn. No behaviour change yet — it builds labelled training data and lets B.3 treat low-confidence intents as escalation candidates.

**Files affected:** `scripts/intent.py`, `scripts/handlers/timer_handler.py`, `tests/test_intent_corpus.py` (new), `scripts/conversation_log.py`.
**Expected impact:** Eliminates the silent TIMER_CANCEL dead end; pattern changes become regression-safe; confidence logging bootstraps B.1 training data from day one.
**Effort:** Medium

### A.8 — Quick wins bundle (each < 1 hour)

| # | Fix | Files | Impact |
|---|---|---|---|
| a | Add `scipy` to `requirements.txt` | `requirements.txt` | Prevents silent TTS failure on fresh install |
| b | Add `require_pin` to `GET /api/logs/download/{filename}` + reject path traversal in `filename` | `scripts/web/routes/logs.py` | Closes an unauthenticated log-exfiltration hole (security) |
| c | Log rotation: `logging.handlers` rotation for `bender.log`; cron/systemd timer pruning `logs/*.jsonl` and `metrics.jsonl` older than N days (after optional copy to `/home/pi/share`) | `scripts/logger.py`, new timer unit | Stops unbounded SD-card growth (SD wear is a real Pi failure mode) |
| d | Rotate the expired HA token (the 401 means weather briefings have been silently serving fallback) + add a `briefing_generation_failed` watchdog alert so token expiry is loud next time | `.env`, `scripts/briefings.py`, `scripts/watchdog.py` | Restores weather; converts silent degradation to alert |
| e | Fix Remote page: add a JSON text variant of `POST /api/remote/ask` (the multipart-audio endpoint is unusable as called from `Remote.svelte`) | `scripts/web/routes/remote.py`, `web/src/pages/Remote.svelte` | Makes the Remote page functional |
| f | Dashboard auto-refresh: 30s polling interval in the status store | `web/src/lib/stores/health.js` (or equivalent), `Dashboard.svelte` | Live metrics without navigation |
| g | Targeted "Refresh Briefings": add `POST /api/actions/refresh-briefings` that deletes cached daily WAVs + triggers regeneration in-process via the IPC file pattern, instead of restarting the service | `scripts/web/routes/actions.py`, `scripts/briefings.py` | Briefing refresh without killing an active session |
| h | Update CLAUDE.md to match reality (ReSpeaker mic, 45 LEDs, 450ms VAD, handler refactor, new intents, `response_hard_timeout_s=45`) | `CLAUDE.md` | Stops every future AI session from reasoning on stale facts |
| i | Fix `scripts/camera.py` docstring (IMX708 → IMX500) | `scripts/camera.py` | Doc accuracy |
| j | Surface `VolumeSlider`, `rebuildResponses()`, `generateStatus()` buttons (all backend-complete, UI-orphaned) | `web/src/pages/Config.svelte` / `Puppet.svelte` | Activates already-built functionality |

**Effort:** Low (each).

---

## Section B: Longer-Horizon Improvements

### B.1 — ML intent classifier trained on accumulated logs

**Problem:** Regex classification cannot handle novel phrasings; every miss becomes a cloud call or wrong handler. Training data is already accumulating (`logs/YYYY-MM-DD.jsonl` records utterance + method per turn), and HANDOVER targets an embedding classifier at ~500 labelled queries.

**Solution:**
1. **Labelling pipeline:** extend `review_log.py` with an `--export-labels` mode producing `(utterance, dispatched_intent, was_correct?)` rows; a small web UI page (or CSV pass) lets Martin correct mislabels. The A.7 confidence field pre-sorts the queue.
2. **Model:** a small sentence-embedding model (e.g. MiniLM-class, ONNX, quantised) + logistic-regression/k-NN head over intent classes. Runs on Pi CPU in a few tens of ms [UNVERIFIED — benchmark on Pi 5]; train on a desktop or HF Jobs.
3. **Hybrid dispatch:** regex first for high-precision exacts (HA_CONTROL device names, TIMER patterns with slots — ML doesn't do slot-filling); ML classifier for everything that would otherwise fall to UNKNOWN; below ML confidence threshold → UNKNOWN → AI routing as today. This keeps the regex layer's determinism where it's strong and adds recall where it's weak.
4. Evaluate against the A.7 golden corpus before cutover; ship behind `intent_ml_enabled: false` config flag.

**Files affected:** `scripts/intent.py`, `scripts/review_log.py`, new `scripts/intent_ml.py`, `models/`, `bender_config.json`.
**Expected impact:** Recovers novel-phrasing misses that currently cost a 3–25s AI round-trip; measurable via the A.6 escalation metric (target: meaningful drop in UNKNOWN-routed turns).
**Effort:** High

### B.2 — Hailo NPU as primary LLM tier (hailo-ollama path)

**Problem:** Hailo LLM works but coexists badly with STT due to KV-Cache exclusivity; STT is consequently on CPU and the acquire/release choreography is convention-enforced. HANDOVER notes `local_llm_url` can point at hailo-ollama (`localhost:8000`).

**Solution:**
1. **Centralise NPU arbitration now:** introduce a `hailo_arbiter.py` with an explicit lock + owner tracking (`acquire(model) / release()`), replacing the by-convention contract between `stt.py` and `ai_local.py`. Even before model-switching improves, this converts silent contention into logged, deterministic queuing — and it's the precondition for re-enabling Hailo STT and VLM safely.
2. **hailo-ollama spike:** point `local_llm_url` at `localhost:8000`; benchmark model-switch penalty (STT↔LLM) per Hailo SDK release. Keep a `metrics.jsonl` timer on switch cost so "revisit when switching improves" becomes a number on STATUS.md instead of a vibe.
3. **Pin the Hailo stack:** apt-pin `hailo-h10-all` at v5.1.1 and add a startup guard that compares installed SDK version against a `bender_config.json` expectation, alerting via watchdog on mismatch (HEF format breakage is currently a silent time bomb).

**Files affected:** new `scripts/hailo_arbiter.py`, `scripts/stt.py`, `scripts/ai_local.py`, `scripts/vlm.py`, `bender_config.json`, apt pin file, `scripts/watchdog.py`.
**Expected impact:** Removes the class of KV-Cache races outright; creates the measurement needed to decide when Hailo STT/VLM can be re-enabled; protects against apt-upgrade breakage.
**Effort:** Medium (arbiter + pinning) / High (full Hailo STT+LLM coexistence).

### B.3 — Adaptive routing thresholds

**Problem:** The scenario classifier (`conversation`/`knowledge`/`creative`) exists but all scenarios map to `local_first` — dead code in effect. Escalation behaviour is static regardless of how badly the local stack is performing (thermal throttling, Hailo cooldown storms, Ollama timeouts).

**Solution:** Built directly on A.6's metrics:
1. Maintain a rolling escalation-rate window (e.g. last 20 AI turns, persisted) per scenario.
2. If escalation rate for a scenario exceeds a threshold (e.g. 60%), flip that scenario's effective routing to `cloud_first` for a cool-off period; flip back after a successful local probe. Honour `local_only` as an absolute override (privacy mode must never auto-escalate).
3. Surface current effective routing per scenario on the Dashboard, with manual override.
4. This finally gives the scenario classifier a job: `creative` (long-form) plausibly deserves a lower local-quality bar than `knowledge` (factual).

**Files affected:** `scripts/responder.py`, `scripts/ai_local.py`, `scripts/config.py`, `scripts/web/routes/status.py`, `Dashboard.svelte`.
**Expected impact:** Worst-case UX (repeated 25s Ollama timeouts during thermal load) self-heals within ~2–3 bad turns instead of persisting all day; cloud usage stays bounded and visible.
**Effort:** Medium

### B.4 — Clip categorisation and response variety

**Problem:** ~88 raw Bender WAVs in `speech/wav/` sit in one "clips" bucket; the frontend already groups by category but no categories exist. Response selection repeats clips with no variety weighting.

**Solution:**
1. Create `speech/wav/index.json` (committed): `{filename: {category, transcript, tags, energy}}`. Bootstrap transcripts by running each WAV through the existing faster-whisper STT offline; hand-correct in one pass; assign categories (greetings, insults, dismissals, laughs, sound-effects, catchphrases).
2. `clip_handler.py` selects by category with a least-recently-used penalty (track last-N plays per session/day) so repeated greetings rotate.
3. Soundboard UI gets the categories for free.
4. Transcripts unlock a future "best-matching clip" responder: embed transcripts (same model as B.1) and serve a real Bender clip when one is semantically close to the user's utterance — more authentic than TTS.

**Files affected:** `speech/wav/index.json` (new), `scripts/handlers/clip_handler.py`, `scripts/prebuild_responses.py`, `web/src/pages/Puppet.svelte`.
**Expected impact:** Perceived personality variety improves immediately; transcript metadata is a reusable asset for semantic clip matching.
**Effort:** Medium (mostly one-time labelling)

### B.5 — Hardware / infrastructure investments

1. **Barge-in feasibility test (high value):** the mic is a *USB* XVF3800; only the speaker uses the WM8960. The documented "cannot listen while playing" constraint dates from the 2-mic Voice Bonnet era and may simply no longer hold. The XVF3800 also ships on-chip AEC and beamforming [UNVERIFIED — confirm firmware/AEC reference path on this unit]. If input and output truly are independent ALSA devices now, barge-in ("Bender, stop") and wake-during-playback become possible, and the entire `open_session`/`close_session` choreography could be simplified to playback-stream lifecycle only. A half-day spike (open mic stream during playback, check for errors/garbage) settles this. Highest strategic upside in this section.
2. **Move logs/models off SD card:** mount point on the existing rpi5-share (or add NVMe via the Pi 5 PCIe — though the Hailo HAT may occupy the slot; verify) for `logs/`, reducing SD wear. Pair with A.8c rotation.
3. **silero-vad evaluation:** replace `webrtcvad-wheels` (single-maintainer fork, no upstream) with silero-vad ONNX — better noise robustness reported [UNVERIFIED — needs on-device A/B] and removes a fragile dependency. Also usable as the wake-loop VAD pre-gate (A.5).
4. **IMX500 on-chip NPU for vision:** offload object detection to the camera's own NPU, freeing the Hailo entirely for STT+LLM and sidestepping the KV-Cache three-way contention that forced VLM off. The IMX500 runs quantised detection models on-sensor; pair detection labels with the LLM for scene descriptions instead of a full VLM [UNVERIFIED — model zoo fit for this use case needs evaluation].
5. **UPS / power-loss safety:** `timers.json` is atomic, but SD corruption on power loss is the bigger risk; a small UPS HAT or overlayfs-root with persistent data partition is worth considering for an always-on appliance.

**Effort:** Spike-sized each; barge-in test first.

---

## Section C: Picovoice Replacement Analysis (URGENT — June 30, 2026)

**18 days remain.** On July 1, `pvporcupine.create()` raises an auth exception inside the outer reinit loop → continuous loop-crash → total voice outage. This is the only item in this review with a hard external deadline.

### Candidate evaluation

#### 1. openWakeWord (primary candidate)
- **Detection accuracy:** Pre-trained models (hey_jarvis etc.) are generally regarded as usable for hobbyist deployments; custom models trained on synthetic TTS data are the project's documented path. Specific FA/FR rates: [UNVERIFIED — needs on-device testing; no figures in the brief].
- **False positives:** Score-threshold based (~0.5 start), tunable; consecutive-frame confirmation and VAD gating (A.5) stack on top. Home has TV + child noise — empirical tuning required (brief, open question 3).
- **RPi5 aarch64 / Python 3.13:** **[UNVERIFIED — explicitly flagged as unconfirmed in the brief, open question 1. This is the gating risk and must be tested first.]** ONNX Runtime itself has aarch64 wheels; the question is openWakeWord's dependency set on 3.13.
- **Python API complexity:** Low. `Model(wakeword_models=[...])`, feed 1280-sample 16kHz mono frames, read score dict. Maps almost 1:1 onto the existing wake loop.
- **Hailo NPU potential:** Models are ONNX — theoretically compilable to HEF, but the wake loop must run *continuously* while the KV-Cache is needed elsewhere; keeping wake on CPU is architecturally correct. Not a real consideration.
- **Migration complexity:** Lowest of all options. Existing scoped plan (`docs/superpowers/plans/2026-05-22-openwakeword-migration.md`); only `wait_for_wakeword()` changes; sample rate identical (16kHz); frame size 512→1280; stereo `[::2]` downmix reusable.

#### 2. Vosk keyword spotting
- **Accuracy:** Vosk is a full STT engine; keyword spotting via constrained grammar. Tends to higher CPU load than a dedicated wake model and weaker far-field wake performance [UNVERIFIED — needs testing].
- **False positives:** Grammar-constrained decoding can fire on phonetically similar speech; no native score-threshold semantics as clean as OWW's.
- **aarch64:** Supported (pip wheels exist for aarch64) [UNVERIFIED on Python 3.13].
- **API complexity:** Medium — `KaldiRecognizer` with grammar JSON; continuous decode loop.
- **Migration:** Medium; also adds a ~50MB model and constant STT-grade CPU load to the idle loop — wasteful next to a purpose-built wake model.
- **Verdict:** Viable fallback if OWW fails on 3.13; "hey bender" as a grammar phrase works without any model training, which is its one genuine advantage.

#### 3. Mycroft Precise / microWakeWord / community tools
- **Precise:** effectively unmaintained (TensorFlow 1.x lineage); training pipeline rusted. Not recommended.
- **microWakeWord:** designed for ESP32-class devices (used by ESPHome/HA Voice PE); streaming tflite models, very low footprint. Training pipeline exists and is active. Running its tflite models on the Pi is possible but the Python-host story is less beaten-path than OWW [UNVERIFIED]. Interesting as an *offload* option: a $13 ESP32-S3 running microWakeWord could be a dedicated wake-word satellite, fully decoupling wake from the Pi — but that is new hardware and firmware 18 days before a deadline. Not now; note for B.5.
- **Snowboy:** dead upstream (service shut down years ago); community forks only. Not recommended.

#### 4. Custom ONNX model on synthetic "Hey Bender" TTS data
This is openWakeWord Option A, not a separate engine: OWW's documented training pipeline generates synthetic positives via TTS (the project conveniently owns a Bender TTS voice — synthetic samples can include the Piper bender.onnx voice plus diverse stock TTS voices) + negative noise corpus, ~1–2h desktop compute, outputs a committable ONNX. Accuracy of synthetic-only custom models varies with phrase phonetics — "Bender" is two crisp syllables, plausibly favourable [UNVERIFIED — needs real-world FA/FR measurement].
A from-scratch non-OWW custom model (own CNN/CRNN) is strictly worse: same data problem, none of the pipeline.

#### 5. Other options
- **Wyoming protocol satellite (Home Assistant ecosystem):** run `wyoming-openwakeword` as a separate service; the brief itself names this as the fallback strategy if OWW fails natively on Python 3.13 — the OWW service can run under a different Python (e.g. 3.11 venv or container) and the wake loop becomes a socket client. Adds a service hop but neatly sidesteps the 3.13 risk. Keep as Plan B.
- **Porcupine paid tier:** rejected on principle (vendor lock-in, recurring cost, contradicts offline-first ethos) but it *is* the zero-engineering fallback if everything else slips past June 30. Note it exists; don't plan for it.

### Recommendation

**Migrate to openWakeWord now, in two phases exactly as the existing plan proposes:**
- **Phase 1 (this week):** validate OWW on Pi 5 / Python 3.13; ship with a pre-trained generic model (`hey_jarvis`) behind config. **If 3.13 validation fails → Plan B: wyoming-openwakeword in a Python 3.11 venv/container, wake loop as socket client.**
- **Phase 2 (after deadline pressure):** train custom "Hey Bender" ONNX via the OWW synthetic-TTS pipeline; commit the model to the repo (no key material, unlike `.ppn`).

### Migration checklist

**1. Dependency changes (`requirements.txt`):**
- Remove: `pvporcupine==4.0.2`, `pvrecorder==1.2.7`
- Add: `openwakeword` (pin to the latest release at install time; verify it pulls a working `onnxruntime` aarch64 wheel for Python 3.13 — if only the tflite path works on this platform, that is acceptable, OWW supports both) [version: UNVERIFIED — confirm at install]

**2. Compatibility spike (DO FIRST, before any code change):**
```
venv/bin/pip install openwakeword
venv/bin/python -c "import openwakeword; from openwakeword.model import Model; \
  Model(wakeword_models=['hey_jarvis']); print('OK')"
```
Run on the Pi. If import or model load fails on Python 3.13 → switch to Plan B (Wyoming) immediately; do not burn days patching.

**3. Code changes:**

| File | Change |
|---|---|
| `scripts/wake_converse.py` | Replace `import pvporcupine` + `pvporcupine.create(...)` block in `wait_for_wakeword()`. New loop: read **1280-sample** frames (stereo: read 2560 samples, downmix `pcm[::2]` — verify stride on the reSpeaker path, brief open question 4); `scores = oww_model.predict(frame)`; trigger when `scores[model_name] >= cfg.oww_threshold` for `cfg.oww_consecutive_frames` (default 2) consecutive frames; call `oww_model.reset()` after trigger to clear internal state. Keep stall detection, sd_notify heartbeat, and left-channel extraction unchanged. |
| `scripts/config.py` | Remove `porcupine_access_key`; add `oww_model_path: str = "models/hey_jarvis.onnx"` (later `models/hey_bender.onnx`), `oww_threshold: float = 0.5`, `oww_consecutive_frames: int = 2`, `oww_inference_framework: str = "onnx"` |
| `.env.example` | Remove `PORCUPINE_ACCESS_KEY` |
| `bender_config.json` | Add the `oww_*` keys |
| `CLAUDE.md` | Update env-var table, wake word description, dependency list |
| `scripts/hey-bender.ppn` | Delete from device after cutover (contains key material) |
| Setup/deploy | Add OWW model file deployment: commit the ONNX to `models/` in git (it contains no secrets, unlike `.ppn`) — solves brief open question 6 with the simplest mechanism; auto-deploy timer already syncs it |

**4. Testing detection quality BEFORE removing pvporcupine:**
1. **Parallel shadow mode:** for the transition, run *both* engines in `wait_for_wakeword()` — Porcupine remains authoritative; OWW scores are computed on the same PCM (buffer 512-sample frames into 1280-sample windows) and logged (`oww_score`, `porcupine_hit`) to `metrics.jsonl`. Run 3–7 days of real household audio.
2. Analyse: OWW hits with no Porcupine hit = candidate false positives (or recall Porcupine missed — listen to a sample); Porcupine hits with low OWW score = candidate false negatives. Tune `oww_threshold` / `oww_consecutive_frames` from this data.
3. Bench CPU: confirm the OWW predict loop on 80ms frames keeps idle CPU acceptable on the Pi 5 (expect single-digit % [UNVERIFIED]).
4. Flip authority to OWW via a config flag (`wake_engine: "oww" | "porcupine" | "shadow"`); keep Porcupine code path for one week of fallback, then delete. **Hard floor: authority must flip by ~June 27** regardless of tuning completeness — an imperfect threshold beats a dead pipeline.

**5. Effort & risk:**
- **Effort:** Low–Medium. ~1 day for spike + core swap; ~2–4 days shadow-mode soak; Phase 2 custom model ~1–2 days including desktop training.
- **Risk:** Medium overall. Dominant risk is the unvalidated Python 3.13/aarch64 combination (mitigated by doing the spike first and having the Wyoming Plan B); secondary risks are threshold tuning in a noisy home (mitigated by shadow mode + A.5 mitigations) and the 1280-sample stereo-stride verification (mitigated by a unit test on the downmix slice).

---

## Section D: Architecture Observations

### Strengths worth preserving

- **The response priority chain (static → pre-gen → promoted → handler → local AI → cloud) with log-driven promotion** is the standout design. It converts runtime cost into build-time cost based on real usage data — a genuinely good pattern that most voice assistants lack.
- **Handler registry + `None`-falls-through dispatch** keeps intent routing open for extension and closed for modification; the TIME intent addition apparently touched only `intent.py` + one handler, which validates the design.
- **Decoupling discipline:** audio→LED via `on_chunk` callback injection, `ConversationSession` extraction, the web router split, centralised device discovery — the May 2026 refactor cycle left the codebase in materially better shape than the CLAUDE.md-era monolith.
- **Defence-in-depth on availability:** systemd watchdog + sd_notify, stall detection, hard inference timeout, TTL-cached briefings with fallback WAVs, atomic timer persistence. The 1.5-day silent freeze clearly taught the right lessons.
- **File-based IPC** (session/end/abort files) is unfashionable and exactly right for two cooperating local services — zero dependencies, debuggable with `ls`.

### Structural risks not covered above

- **The Hailo KV-Cache contract is convention-only across three modules** (`stt.py`, `ai_local.py`, `vlm.py`), and the brief confirms `release()` may not actually free the VDevice (SDK-internal references). This is the project's most fragile invariant: a single missed release produces confusing, intermittent init failures attributed to the wrong subsystem. The B.2 arbiter should be pulled forward if Hailo STT or VLM is ever re-enabled.
- **Documentation drift is systemic, not incidental.** CLAUDE.md disagrees with the code on the microphone, LED count, silence timings, handler structure, intent list, and a timeout value — six discrepancies in one table. For a project developed primarily via AI sessions, stale CLAUDE.md is *worse* than no CLAUDE.md: every session starts by confidently believing falsehoods. A "docs updated in the same commit" check (even a CI grep for renamed files referenced in CLAUDE.md) would pay for itself.
- **IPC files have no staleness/race handling described.** If the voice service crashes between writing `session_file` and clearing it, the web UI presumably shows a phantom session; `abort_file` left behind would abort the next legitimate playback. Files should carry a timestamp/PID and be ignored if stale.
- **`hailo-h10-all` via apt with no pin** is a silent compatibility time bomb (HEF format breakage) — covered in B.2 but worth flagging as the only dependency that can break the system through a routine `apt upgrade` with no Python-side signal.
- **Security posture of the web service is PIN-plus-LAN-trust**, and the unauthenticated log download (A.8b) shows the PIN gate isn't uniformly applied. One audit pass asserting `require_pin` on every route (a test that introspects the FastAPI app's routes) would lock this down permanently.
- **No test suite is mentioned anywhere in the brief.** For a system with this many timing-sensitive invariants (sample-rate exclusivity, KV-Cache sequencing, frame sizes), even a thin layer — intent corpus (A.7), downmix stride, config parsing, route auth — would disproportionately de-risk AI-driven development sessions.

### Unexpected component interactions

- **LED updates are synchronous SPI writes on every ~11.6ms audio chunk** (`pixels.show()` inside the playback hot path). At 45 LEDs this is more data per frame than the original 12; any SPI bus contention or a slow `show()` stalls the audio write loop → underrun/stutter. Worth a one-off timing measurement of `show()` and, if >2–3ms, moving LED writes to a worker thread fed by a latest-value slot (drop frames, never block audio).
- **The thinking-sound gap and streaming-TTS gap compound:** today the user experiences silence → thinking clip → full response. A.1 + A.2 together transform the same backend latency into acknowledge → first sentence — the system's perceived speed lives almost entirely in these two fixes.
- **`VisionHandler` blocking inside the turn while `FutureVisionProvider` exists** means the codebase contains both the right pattern and the wrong one for the same job; the blocking path stacks onto `response_hard_timeout_s` (45s) and could hold the session hostage. When VLM returns, unify on the future-based provider.
- **Wake-word engine choice interacts with the STT hallucination filter:** ghost wakes currently produce a turn in which Whisper hallucinates from near-silence — which is exactly what the hallucination filter then catches. The system is accidentally measuring its own false-wake rate and discarding the evidence; A.5(3) just writes it down.
- **The ReSpeaker migration quietly invalidated the founding audio constraint.** The open/close session choreography, the no-barge-in limitation, and Hard Constraint 1's severity all derive from "mic and speaker share the WM8960" — which is no longer the hardware reality. This is the single most consequential thing a fresh pair of eyes notices: the architecture is faithfully honouring a constraint that may have left the building (B.5.1). Verify it; if confirmed independent, a significant simplification of `audio.py` and a genuine barge-in feature both unlock.

---

*End of review.*
