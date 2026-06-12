# BenderPi Fable Review Workflow — Design Spec

**Date:** 2026-06-12  
**Status:** Approved  
**Goal:** Produce a comprehensive improvement plan for BenderPi by running parallel Sonnet gather agents to compile a project brief, then feeding it to a single Fable agent for deep architectural review.

---

## Motivation

BenderPi has reached a stable, well-architected state. This review is a deliberate step back to identify performance and usability improvements across every layer — from wake word detection to TTS delivery — before the next development cycle. Fable is used exactly once to maximise insight-per-token.

**Urgent driver:** Picovoice (pvporcupine) is eliminating its free tier on **June 30, 2026**. A migration path to an open-source wake word engine must be part of the output. An existing migration plan (`docs/superpowers/plans/2026-05-22-openwakeword-migration.md`) provides a starting point.

---

## Architecture

### Phase 1 — Parallel Gather (6 Sonnet agents)

Six agents run concurrently, each reading a specific subsystem and returning a structured markdown summary. No agent reads more than it needs to.

| Agent label | Source files | Scope |
|---|---|---|
| `gather:audio-hardware` | `audio.py`, `stt.py`, `leds.py`, `config.py` | WM8960 single-sample-rate constraint, Hailo STT (Whisper-Base), ReSpeaker XVF3800, device discovery, LED sync |
| `gather:ai-routing` | `ai_local.py`, `ai_response.py`, `responder.py`, `bender_config.json` | Ollama CPU LLM, Hailo NPU LLM, Claude fallback chain, quality-check escalation, KV-Cache conflict |
| `gather:intent-handlers` | `intent.py`, `handlers/` (all), `session.py`, `wake_converse.py` | Intent patterns + false positive notes, handler registry, ConversationSession, turn dispatch |
| `gather:tts-pipeline` | `tts_generate.py`, `briefings.py`, `prebuild_responses.py` | Piper subprocess pipeline, 22050→44100 resampling, pre-gen + promoted WAVs, briefing cache |
| `gather:web-ui` | `web/src/` (all .svelte + .js), `scripts/web/routes/` | Frontend feature set, API surface, Svelte/Tailwind stack, puppet mode, health dashboard |
| `gather:deps-issues` | `requirements.txt`, `HANDOVER.md`, `docs/superpowers/plans/2026-05-22-openwakeword-migration.md` | All deps + versions, known issues, current limitations, Picovoice deadline, existing OWW plan |

Each agent returns its findings as structured markdown — hardware facts, identified bottlenecks, pain points, and anything notable about the subsystem.

### Phase 2 — Compile (1 Sonnet agent)

Synthesizes all 6 summaries plus the CLAUDE.md project documentation into a single brief: `docs/benderpi-review-brief.md`.

**Brief structure:**
1. Project overview + design philosophy (offline-first, Bender personality, HA integration)
2. Hardware stack + hard constraints (RPi5, Hailo-10H, WM8960, ReSpeaker, 12x WS2812B LEDs)
3. Software dependencies — custom modules and third-party packages with versions
4. Architecture + full data flow: wake word → STT → intent → response priority chain → TTS → playback
5. Pain points by pipeline stage:
   - **Wake word:** ghost triggering, pvporcupine free-tier deadline (June 30)
   - **STT:** Hailo KV-Cache conflicts, ~5s first-wake delay (pre-warming helps)
   - **Intent:** keyword/regex false positives, no ML classifier yet
   - **AI routing:** Ollama latency (up to 25s), quality-check escalation rate unknown
   - **TTS:** Piper subprocess per generation, no streaming, thinking sound gap
   - **Audio:** WM8960 single sample rate — cannot overlap input/output streams
6. Current mitigations already in place
7. Future considerations already identified (from HANDOVER.md)
8. **URGENT — Picovoice sunset:** timeline, existing OWW migration plan summary, open questions

### Phase 3 — Fable Review (1 Fable agent)

Reads `docs/benderpi-review-brief.md` in full. Writes `docs/benderpi-fable-review.md`.

**Output structure:**

**Section A — Near-term improvements (implementable by Sonnet/Opus)**  
Specific, actionable items. Each item includes: what to change, which files are affected, expected impact, and any risk/constraint. Expected coverage:
- Streaming LLM → TTS (start speaking first sentence before full response completes)
- Thinking sounds during generation (split `get_response()` into classify + generate phases)
- Persistent Piper process (`--json-input` mode — verify and implement)
- Intent classifier improvements (ML-based or embedding similarity)
- Wake word sensitivity tuning / false positive reduction
- Quality check threshold tuning and escalation rate visibility
- Any quick wins in the web UI or logging

**Section B — Longer horizon**  
Strategic investments: local ML routing classifier (training data already accumulating), Hailo NPU for LLM when model-switching stabilises, adaptive routing thresholds, clip categorisation, hardware additions (motorised model, better speaker), camera/vision pipeline.

**Section C — Picovoice replacement analysis (URGENT)**  
Evaluation of alternatives given the June 30, 2026 deadline:
- OpenWakeWord (primary candidate — existing plan exists)
- Vosk KWS
- Custom model trained on Hailo NPU HEF
- Any other viable options

For each: accuracy/false-positive profile, RPi5/aarch64 support, Hailo NPU compatibility, migration complexity, recommended path. Final recommendation with concrete next steps.

---

## Output Files

| File | Written by | Purpose |
|---|---|---|
| `docs/benderpi-review-brief.md` | Phase 2 Sonnet agent | Compiled project brief — standalone useful artifact |
| `docs/benderpi-fable-review.md` | Phase 3 Fable agent | Improvement plan — handed off to Sonnet/Opus for implementation |

---

## Constraints

- Fable runs **exactly once** — the brief must be complete and well-structured before Phase 3 fires
- Total agents: 8 (6 gather + 1 compile + 1 review)
- The workflow is implemented using the `Workflow` tool with `pipeline()` for Phase 1 and sequential `agent()` calls for Phases 2 and 3
- Phase 1 agents are read-only (no file writes)
- The compile agent writes `docs/benderpi-review-brief.md`
- The Fable agent writes `docs/benderpi-fable-review.md`

---

## Success Criteria

- `docs/benderpi-review-brief.md` accurately represents the full BenderPi stack with no major gaps
- `docs/benderpi-fable-review.md` contains actionable near-term items a subsequent Sonnet session can execute directly, plus strategic longer-horizon suggestions
- Picovoice replacement has a clear recommended path with migration steps
- The workflow completes without requiring Fable to ask clarifying questions (brief is self-contained)
