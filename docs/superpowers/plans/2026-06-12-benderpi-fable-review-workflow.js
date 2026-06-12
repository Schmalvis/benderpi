export const meta = {
  name: 'benderpi-fable-review',
  description: 'Parallel Sonnet gather + compile brief + Fable deep review for BenderPi',
  phases: [
    { title: 'Gather', detail: 'Seven parallel Sonnet agents read BenderPi subsystems' },
    { title: 'Compile', detail: 'Synthesize findings into structured project brief' },
    { title: 'Review', detail: 'Fable reads brief and writes improvement plan' },
  ],
}

const REPO = '/home/pi/projects/benderpi'
const BRIEF_PATH = `${REPO}/docs/benderpi-review-brief.md`
const REVIEW_PATH = `${REPO}/docs/benderpi-fable-review.md`

phase('Gather')

const gathered = await parallel([
  () => agent(`You are gathering information about the BenderPi audio and hardware subsystem.

Read these files (use the Read tool on each):
- ${REPO}/scripts/audio.py
- ${REPO}/scripts/stt.py
- ${REPO}/scripts/leds.py
- ${REPO}/scripts/config.py

Return a structured markdown summary covering:
1. WM8960 audio codec constraint — single sample rate, cannot overlap input (16000Hz mic) and output (44100Hz playback) streams simultaneously
2. PyAudio session management — open_session(), close_session(), 100ms warm-up/tail silence, DAC pop prevention
3. Hailo NPU STT — Speech2Text class, pre-warming at startup, KV-Cache acquisition/release lifecycle, first-wake ~5s delay issue
4. ReSpeaker XVF3800 4-Mic Array — device discovery by name (mic_shared hint), stereo to mono left-channel downmix for Porcupine
5. LED sync — WS2812B via SPI GPIO10, on_chunk/on_done callback decoupling, listening (blue) / talking (white) colours, RMS normalisation
6. Any latency contributions, fragile assumptions, or bottlenecks you observe

Do not write or modify any files.

Return ONLY the structured markdown. No preamble.`,
  {label: 'gather:audio-hardware', phase: 'Gather', model: 'sonnet'}),

  () => agent(`You are gathering information about the BenderPi AI routing subsystem.

Read these files (use the Read tool on each):
- ${REPO}/scripts/ai_local.py
- ${REPO}/scripts/ai_response.py
- ${REPO}/scripts/responder.py
- ${REPO}/bender_config.json

Return a structured markdown summary covering:
1. Full AI response priority chain: static WAVs to pre-gen TTS to Hailo NPU LLM to Ollama CPU (Qwen2.5-1.5B) to Claude cloud (Haiku)
2. LocalAIResponder — warm_up() background pre-load, quality check logic (hedge phrases, min-length threshold), QualityCheckFailed exception flow
3. Hailo LLM — _load() with 60s cooldown retry, VDevice/KV-Cache lifecycle, atexit cleanup, conflict with STT NPU usage
4. Routing config — ai_backend (hybrid/local_only/cloud_only), ai_routing per scenario (conversation/knowledge/creative), local_llm_timeout=25s
5. Scenario classifier (_classify_scenario) — how it decides which routing rule applies per query
6. Latency breakdown: typical Ollama response time, escalation overhead, cloud fallback timing

Do not write or modify any files.

Return ONLY the structured markdown. No preamble.`,
  {label: 'gather:ai-routing', phase: 'Gather', model: 'sonnet'}),

  () => agent(`You are gathering information about the BenderPi intent classification and handler subsystem.

Read these files (use the Read tool on each):
- ${REPO}/scripts/intent.py
- ${REPO}/scripts/session.py
- ${REPO}/scripts/wake_converse.py
- ${REPO}/scripts/handler_base.py
Then list the contents of ${REPO}/scripts/handlers/ and read each .py file there.

Return a structured markdown summary covering:
1. All intent types and their pattern lists; classify() vs classify_simple() distinction; CONTEXTUAL_PATTERNS
2. Known false positive/negative issues (e.g. timer vs HA_CONTROL overlap, greeting patterns firing mid-sentence)
3. Handler registry — Handler base class interface (intents attribute, handle() method), how Responder discovers/dispatches
4. All handler types with their scope: ClipHandler, PregenHandler, PromotedHandler, WeatherHandler, NewsHandler, HAHandler, TimerHandler, TimeHandler
5. ConversationSession — full turn loop, TurnResult dataclass (should_end + end_reason), VisionProvider Protocol, file-based IPC
6. wake_converse.py — wake word detection loop, STT handoff, xvf_dsnoop stereo path, session lifecycle

Do not write or modify any files.

Return ONLY the structured markdown. No preamble.`,
  {label: 'gather:intent-handlers', phase: 'Gather', model: 'sonnet'}),

  () => agent(`You are gathering information about the BenderPi TTS pipeline subsystem.

Read these files (use the Read tool on each):
- ${REPO}/scripts/tts_generate.py
- ${REPO}/scripts/briefings.py
- ${REPO}/scripts/prebuild_responses.py

Return a structured markdown summary covering:
1. Piper TTS pipeline — exact subprocess command, 22050 to 44100Hz resampling via scipy.signal.resample_poly, warm-up approach, --json-input status (verified or fallback?)
2. Latency breakdown — subprocess spawn time, inference time, resampling time; typical end-to-end generation duration
3. Pre-generated WAVs — index.json structure, categories of pre-built responses, promotion workflow (review_log.py to prebuild_responses.py)
4. Briefings system — BriefingSource dataclass, TTL caching (weather 30min, news 2hr, time 60s), background refresh thread, fallback WAV behaviour
5. Streaming opportunity — at what point in the pipeline could TTS begin before full LLM response is complete?
6. Persistent Piper process potential — what would need to change to keep Piper alive between calls?

Do not write or modify any files.

Return ONLY the structured markdown. No preamble.`,
  {label: 'gather:tts-pipeline', phase: 'Gather', model: 'sonnet'}),

  () => agent(`You are gathering information about the BenderPi web UI subsystem.

Read these files (use the Read tool on each):
- ${REPO}/web/src/App.svelte
- ${REPO}/web/src/lib/api.js
- ${REPO}/web/src/pages/Dashboard.svelte
- ${REPO}/web/src/pages/Puppet.svelte
- ${REPO}/web/src/pages/Config.svelte
- ${REPO}/web/src/pages/Logs.svelte
- ${REPO}/web/src/pages/Remote.svelte
- ${REPO}/scripts/web/app.py
- ${REPO}/scripts/web/auth.py
Then list ${REPO}/scripts/web/routes/ and read each .py file there.

Return a structured markdown summary covering:
1. Complete feature inventory — every page, all functionality, what data each surfaces
2. Full API surface — all HTTP endpoints, method, purpose, auth requirement
3. Authentication — PIN-based flow, session token handling, middleware approach
4. Puppet mode — end-to-end: text input to which API endpoint to how it reaches TTS on the Pi
5. Health dashboard — metrics surfaced, polling interval, alert display logic
6. UX gaps — features that feel incomplete, missing functionality, confusing flows

Do not write or modify any files.

Return ONLY the structured markdown. No preamble.`,
  {label: 'gather:web-ui', phase: 'Gather', model: 'sonnet'}),

  () => agent(`You are gathering information about BenderPi's dependencies, known issues, prior architectural decisions, and the Picovoice sunset.

Read these files (use the Read tool on each):
- ${REPO}/requirements.txt
- ${REPO}/HANDOVER.md
- ${REPO}/docs/superpowers/plans/2026-05-22-openwakeword-migration.md
- ${REPO}/docs/ARCHITECTURAL_REVIEW_2026-05-14.md
- ${REPO}/docs/benderpi-technical-profile.md
- ${REPO}/docs/superpowers/plans/2026-04-01-streaming-tts.md

Return a structured markdown summary covering:
1. Complete dependency table — read exact versions from requirements.txt (do not assume), columns: package, version, purpose, risk level (High/Medium/Low)
2. Known issues — copy the Known Issues section from HANDOVER.md verbatim, then annotate each with severity (Critical/Major/Minor)
3. Future considerations — copy the Future Considerations section from HANDOVER.md verbatim
4. Picovoice sunset: read requirements.txt to confirm exact pvporcupine and pvrecorder versions. Identify every file that imports or references pvporcupine/pvrecorder. What will break the moment the API key stops working?
5. OpenWakeWord migration plan — full summary of docs/superpowers/plans/2026-05-22-openwakeword-migration.md (key differences table, custom wake word problem, open questions not yet resolved)
6. Prior architectural decisions — summarise key findings and decisions from ARCHITECTURAL_REVIEW_2026-05-14.md and benderpi-technical-profile.md. Summarise the streaming TTS design from 2026-04-01-streaming-tts.md (it was planned — include its current status and whether it was implemented)
7. Other dependency risks: webrtcvad-wheels Python 3.13 compat, faster-whisper stability, Hailo SDK version lock

Do not write or modify any files.

Return ONLY the structured markdown. No preamble.`,
  {label: 'gather:deps-issues', phase: 'Gather', model: 'sonnet'}),

  () => agent(`You are gathering information about BenderPi's vision, timer, and observability subsystems.

Read these files (use the Read tool on each):
- ${REPO}/scripts/camera.py
- ${REPO}/scripts/vision.py
- ${REPO}/scripts/vlm.py
- ${REPO}/scripts/timers.py
- ${REPO}/scripts/time_parser.py
- ${REPO}/scripts/metrics.py
- ${REPO}/scripts/watchdog.py
- ${REPO}/scripts/logger.py
- ${REPO}/scripts/generate_status.py
Also read: ${REPO}/scripts/handlers/timer_alert.py and ${REPO}/scripts/handlers/vision_handler.py (if they exist — check with ls first)

Return a structured markdown summary covering:
1. Vision/VLM pipeline — camera.py (IMX500 AI Camera), vision.py (scene analysis), vlm.py (Qwen2-VL-2B on Hailo NPU), VisionHandler — current status (enabled/disabled), lazy scene injection into conversation context
2. Timer subsystem — timers.py (concurrent named timers, persistence via timers.json), time_parser.py (natural language duration parsing), timer_alert.py (play-pause dismissal cycle respecting WM8960)
3. Observability stack — metrics.py (timing/counter metrics to logs/metrics.jsonl), logger.py (structured logging to logs/bender.log), watchdog.py (session liveness checks, configurable thresholds), generate_status.py (STATUS.md auto-generation)
4. Real latency data — if ${REPO}/logs/metrics.jsonl exists, run: tail -50 ${REPO}/logs/metrics.jsonl and extract any measured timing figures (stt_duration, tts_duration, ai_response_time, etc.)
5. Current operational status — what is enabled vs disabled (vlm_enabled=false, hailo_stt_enabled=false in bender_config.json) and why
6. Gaps in observability — what would you want to measure that is not currently instrumented?

Do not write or modify any files.

Return ONLY the structured markdown. No preamble.`,
  {label: 'gather:vision-timers-ops', phase: 'Gather', model: 'sonnet'}),
])

log('All 7 subsystem summaries gathered. Compiling project brief...')

phase('Compile')

const compileResult = await agent(`You are compiling a comprehensive project brief for BenderPi — a Raspberry Pi 5 offline-first voice assistant with the personality of Bender (Futurama), using a Hailo AI HAT+ NPU for on-device inference.

First, read ${REPO}/CLAUDE.md in full using the Read tool.

IMPORTANT — conflict resolution: CLAUDE.md may be outdated on some hardware details (e.g. it may reference Adafruit Voice Bonnet; the current mic is ReSpeaker XVF3800). Where CLAUDE.md conflicts with the subsystem summaries below, the summaries win (they are derived from current code). Note any such discrepancies in Section 2.

Then combine the CLAUDE.md content with the following 7 subsystem summaries into a single project brief. If any summary below reads as "null" or is empty, note the missing section in the brief rather than silently omitting it.

---
SUBSYSTEM SUMMARY 1 — AUDIO + HARDWARE:
${gathered[0]}

---
SUBSYSTEM SUMMARY 2 — AI ROUTING:
${gathered[1]}

---
SUBSYSTEM SUMMARY 3 — INTENT + HANDLERS:
${gathered[2]}

---
SUBSYSTEM SUMMARY 4 — TTS PIPELINE:
${gathered[3]}

---
SUBSYSTEM SUMMARY 5 — WEB UI:
${gathered[4]}

---
SUBSYSTEM SUMMARY 6 — DEPENDENCIES + ISSUES:
${gathered[5]}

---
SUBSYSTEM SUMMARY 7 — VISION, TIMERS + OBSERVABILITY:
${gathered[6]}

---

Write the following markdown document and save it to ${BRIEF_PATH} using the Write tool.

# BenderPi — Project Brief for Architectural Review

## 1. Project Overview
Project purpose, Bender personality, design philosophy (offline-first, privacy, HA integration), current maturity level.

## 2. Hardware Stack
Table: component, role, key constraint. Followed by a HARD CONSTRAINTS subsection listing the non-negotiable limits (WM8960 single sample rate, Hailo KV-Cache exclusivity between STT and LLM, SPI LED data, GPIO limits). Note any discrepancies found between CLAUDE.md and current code here.

## 3. Software Architecture
Module map showing dependencies between scripts/. Key design patterns in use (handler registry, ConversationSession, BriefingSource, hybrid AI routing, IPC files). Explain why each pattern exists.

## 4. Full Pipeline: Wake to Response
Numbered walkthrough of a complete interaction. Each step: what happens, which module, approximate latency contribution where known.
Steps: wake word detection to VAD/buffer to STT to intent classify to handler dispatch OR AI routing to response priority chain to TTS or WAV playback to session continue/end.

## 5. Software Dependencies
Grouped table (Wake Word / STT / Audio / AI / TTS / Web / Utilities / Hardware). Columns: package, version, purpose, risk level.

## 6. Pain Points & Known Issues
Grouped by pipeline stage. Each entry: description, current impact on UX, any existing mitigation.
Stages: Wake Word (ghost triggering, Picovoice sunset), STT (KV-Cache conflicts, latency), Intent (false positives/negatives), AI Routing (Ollama latency, thinking sound gap, escalation opacity), TTS (subprocess overhead, no streaming), Audio (WM8960 constraint), Vision (disabled, KV-Cache conflict), System (SD card accumulation).

## 7. Current Mitigations In Place
Bullet list of what has already been done to address pain points — be specific (e.g. pre-warming, response_hard_timeout_s=20, briefing TTL cache, handler registry decoupling, audio callback decoupling, etc.).

## 8. Future Considerations Already Identified
Copy the Future Considerations section from HANDOVER.md. Then add any additional items surfaced by the subsystem analysis that were not already listed.

## 9. URGENT — Picovoice Free-Tier Sunset (June 30, 2026)
Deadline. Exact packages affected and their versions. Every file that references pvporcupine/pvrecorder. What will break the moment the API key stops working. Full summary of the existing OpenWakeWord migration plan. Open questions not yet resolved by that plan.

After writing the file, return exactly: "Brief written to ${BRIEF_PATH}"`,
{label: 'compile:brief', phase: 'Compile'})

log(compileResult)

phase('Review')

const reviewResult = await agent(`You are performing a comprehensive architectural review of BenderPi.

Read the full project brief at ${BRIEF_PATH} using the Read tool. Read the ENTIRE document before writing anything.

Then write your review to ${REVIEW_PATH} using the Write tool.

# BenderPi — Architectural Review & Improvement Plan

## Executive Summary
Exactly 5 bullet points. The most important findings — one sentence each, ordered by urgency.

## Section A: Near-Term Improvements
Implementable by Claude Sonnet/Opus in the next few development sessions.

For EACH improvement:
### A.N — [Title]
**Problem:** What is wrong or suboptimal (be specific — reference files/functions where relevant)
**Solution:** What to build and how — design approach, not vague suggestion
**Files affected:** Exact paths
**Expected impact:** Concrete and measurable (e.g. "eliminates ~1.5s Piper subprocess spawn per response", "reduces false wake rate by removing single-word trigger matches")
**Effort:** Low / Medium / High

Prioritise by impact/effort. You MUST address at minimum:
- Streaming LLM to TTS (first sentence plays before full response arrives)
- Thinking sounds during AI generation — currently plays AFTER response ready, should play DURING
- Persistent Piper process via --json-input mode
- Wake word ghost-trigger / false positive reduction (non-Picovoice improvements)
- Intent classifier brittleness (keyword patterns, ambiguous overlaps)
- Quality-check escalation visibility (operators cannot see local failure rate)
- Any additional quick wins you identify from the brief

## Section B: Longer-Horizon Improvements
Strategic investments (weeks to months). Same format as Section A.

You MUST address at minimum:
- ML intent classifier (training data already accumulating in logs/YYYY-MM-DD.jsonl)
- Hailo NPU for LLM when model-switching penalty reduces
- Adaptive routing thresholds (auto-escalate if local failure rate exceeds threshold)
- Clip categorisation and improved response variety
- Hardware or infrastructure improvements worth considering

## Section C: Picovoice Replacement Analysis (URGENT — June 30, 2026)

Evaluate:
1. OpenWakeWord (primary candidate — existing migration plan already scoped)
2. Vosk keyword spotting
3. Precise / microWakeWord / other community wake word tools
4. Custom ONNX model trained on synthetic "Hey Bender" TTS data
5. Any other viable option you identify

For each: detection accuracy profile, false positive rate, RPi5 aarch64 support, Python API complexity, Hailo NPU potential, migration complexity from pvporcupine.

IMPORTANT — you do not have web access. For any accuracy figure or benchmark not present in the project brief, mark it explicitly as [UNVERIFIED — needs testing] rather than stating it as fact.

Make ONE clear recommendation. Then provide a concrete migration checklist:
- Exact pip packages to remove and add (with versions if known)
- Exact files to modify and what changes are needed
- How to test detection quality before removing pvporcupine entirely
- Estimated effort and risk

## Section D: Architecture Observations
Strengths worth preserving. Structural risks not covered above. Unexpected component interactions. Anything a senior engineer would flag when reading the codebase for the first time.

Write the complete document to ${REVIEW_PATH}. After writing, return exactly: "Review written to ${REVIEW_PATH}"`,
{label: 'review:fable', phase: 'Review', model: 'fable'})

log(reviewResult)

return {
  brief: BRIEF_PATH,
  review: REVIEW_PATH,
}
