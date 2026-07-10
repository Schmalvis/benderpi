# BenderPi Implementation Plan
Last updated: 2026-03-22

Priority: P1 (critical) → P2 (high) → P3 (medium) → P4 (low/future)

---

## Phase 1 — Active Issues (Fix Now)

### P1: Latency regression from sentence splitting
**Problem:** TTS is noticeably slower after the sentence-splitting optimisation.
Each sentence spawns a separate Piper subprocess (~200ms startup overhead per sentence).
A 3-sentence response now has 3× subprocess launches.
**Fix:** Warm Piper process approach — keep a single Piper process alive and
pipe sentences through stdin, or parallelise sentence generation using
`concurrent.futures.ThreadPoolExecutor`.
**Files:** `scripts/tts_generate.py`
**Effort:** 2–3 hrs

### P1: STT accuracy drop
**Problem:** STT accuracy has dropped since recent changes.
**Likely cause:** Not directly caused by code changes — Whisper-Base on Hailo is
unchanged. Most likely: response latency increase (see above) means Bender is
finishing speech later, reducing the user's window before silence timeout.
**Fix:** After fixing latency above, retest. If still degraded, check Hailo device
logs for model errors / re-probe STT pipeline.
**Files:** `scripts/stt.py`, `scripts/wake_converse.py`
**Effort:** 1 hr investigation

---

## Phase 2 — High Impact (Hailo NPU)

### P2: Run LLM on Hailo NPU (Qwen2.5-1.5B) — SHIPPED

**Status: done.** `ai_backend: "hybrid"` with Hailo GenAI as the primary local LLM,
Ollama CPU as fallback, landed in `7736116` (`feat: Hailo on-chip LLM as primary AI
backend`) and went through several rounds of hardening as real coexistence issues
with Hailo STT surfaced on-device (KV-Cache contention causing STT/LLM to fight over
the chip) — see `304cd45`, `4d3e5b4`, `922a1b4`, `001db30`, `e9700d2`, culminating in
`9cd3d93` (`fix(hailo): harden STT+LLM VDevice coexistence, re-enable Hailo STT`),
which is the commit to treat as "the coexistence problem is solved" for this feature.

**Follow-on (this review cycle, `28bb70b`):** opt-in `llm_warm_session` config flag
holds the Hailo LLM VDevice across conversation turns (released at session `end()`)
instead of reloading the HEF after every AI turn — see `llm_warm_session` in
CLAUDE.md's Runtime Config table for the tradeoff (+3–5s HEF reload avoided per turn,
opt-in and hardware-gated because it assumes Whisper + Qwen HEFs coexist cleanly;
default is still `false`).

**Files:** `scripts/ai_local.py`, `scripts/responder.py`, `scripts/session.py`,
`bender_config.json`

### P2: Parallelise STT + LLM warm-up
Once LLM runs on Hailo alongside STT, both can share the device (VDevice shared
access already implemented). Explore pre-warming the LLM model between turns to
reduce cold-start latency.
**Effort:** 2 hrs

---

## Phase 3 — Vision Capabilities (Requires Camera)

### P3: Attach RPi Camera + person detection
**Hardware needed:** RPi Camera Module 3 (or compatible)
**Models available on H10H:** `yolov8m`, `scrfd_10g` (face detection), `yolov8m_pose`

**Sub-tasks:**
1. Mount camera on Bender chassis, connect to RPi camera port
2. Install hailo-apps detection pipeline (`hailo-download-resources --group detection`)
3. Wire person-detection output to NeoPixel reactions:
   - Person detected → eyes light up (amber glow)
   - Person leaves frame → eyes dim
4. Integrate with greeting logic: trigger wake-word prompt when person approaches

**Files:** new `scripts/vision.py`, `scripts/leds.py`
**Effort:** 4–6 hrs (excluding physical camera mount)

### P3: Face recognition → personalised greetings
**Models:** `scrfd_10g` (face detection) + `arcface_mobilefacenet` (face recognition)
Build a small face registry (enrol Martin + household members by name).
Bender addresses people by name on recognition.
**Effort:** 6–8 hrs
**Depends on:** P3 camera work above

### P3: Visual LLM — Bender can describe what he sees
**Discovery:** `Qwen2-VL-2B-Instruct` (vision language model) is available for H10H.
Ask Bender "what can you see?" and he describes the room in character.
**Effort:** 4–6 hrs
**Depends on:** Camera attached, P2 Hailo LLM work done

### P3: Gesture control
**Model:** `yolov8m_pose` (pose estimation on H10H)
Recognise specific hand gestures to trigger automations without speech:
- Wave → lights on
- Thumbs down → lights off
- Crossed arms → end session
**Effort:** 6–8 hrs

---

## Phase 4 — TTS Improvement

### P4: Evaluate Kokoro with built-in voice
Install `kokoro-onnx` and benchmark on Pi5. Compare latency and naturalness
against optimised Piper. If latency is acceptable (~1–2s), consider offering
as a configurable alternative voice via web UI.
**Note:** No Bender-sounding voice available — would use a built-in male voice.
**Effort:** 2–3 hrs evaluation

### P4: Collect more Bender audio (towards Kokoro custom voice)
StyleTTS2/Kokoro custom voice fine-tuning needs ~10hrs of clean audio.
Current corpus: ~5 mins (29 clips).
Log all Bender TTS output to a review queue. After ~6 months at moderate
usage, a viable corpus may be available for a HuggingFace training job.
**Effort:** Ongoing / passive

---

## Phase 5 — Long-term / Intelligence

### P5: ML intent routing classifier
Replace keyword-based `_classify_scenario()` in `responder.py` with an
embedding-based classifier trained on real usage data.
Current logging captures `ai_routing` per turn — need ~500+ labelled examples.
**Train on:** HuggingFace Jobs using collected conversation logs
**Effort:** 4–6 hrs (once data is available)
**Data threshold:** ~500 queries (currently collecting)

### P5: Adaptive silence timeout
Current 8s silence timeout feels arbitrary. Track turn-taking patterns from
logs and auto-tune per time-of-day / session length.
**Effort:** 2–3 hrs

### P5: Whisper-Small/Medium upgrade
**Status:** Whisper-Small and Medium are NOT currently available as H10H HEFs
in the hailo-apps model zoo (only Base is listed). Monitor Hailo model zoo
releases — upgrade when available.
**Action:** Check with each hailo-apps update (`pip install --upgrade hailo-apps`)

### P5: Mobile base / obstacle awareness
`scdepthv3` (depth estimation) is available on H10H — relevant if Bender
gets wheels. Pairs with person detection for safe navigation.

---

## Known Issues (Backlog)

| Issue | Severity | Notes |
|---|---|---|
| Thinking sound plays after response, not during generation | Medium | Needs `get_response()` split into classify + generate |
| Piper --json-input mode unverified on Pi | Low | Using warm-up fallback; monitor |
| Intent false positives (reduced but not eliminated) | Low | Ongoing tuning |
| Conversation logs accumulating on SD card | Low | Set up periodic backup to share drive |

---

## Completed This Session (2026-03-22)

- ✅ Deployed local LLM (Ollama + Qwen2.5:1.5b)
- ✅ Installed all post-deploy steps (sudoers, timer clips, Ollama, deps)
- ✅ Fixed DISMISSAL patterns (stop/shut up/be quiet now end session)
- ✅ HA pronoun context — "turn them off" resolves to last controlled entity
- ✅ De-esser applied to TTS pipeline (~4dB rolloff above 7kHz)
- ✅ Piper optimised: noise_scale 0.9, noise_scale_w 1.2, sentence splitting, text preprocessing
- ✅ Reduced local LLM timeout 6s → 3s
- ✅ HANDOVER.md restructured and committed
