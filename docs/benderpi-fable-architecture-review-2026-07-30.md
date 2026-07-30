# BenderPi Architecture + Model-Capability Review — 2026-07-30

Follow-up to `docs/benderpi-fable-review-2026-07-09.md`. That review's reliability agenda
(fail-loud, watchdogs, deploy safety) shipped and is not re-litigated here. This review is
about latency, models, and the Hailo resource architecture — and it is grounded in
measurements taken on the live device today, including two spikes run against the
Hailo-10H itself.

**Verification legend:** ✅ measured/ran it on-device today · 📊 from live metrics/logs ·
📖 read in source/vendor code · ❓ inferred, needs a soak or test.

---

## Executive summary

The single question that was blocking three features — *can Whisper and Qwen HEFs coexist
on the Hailo-10H?* — is now **answered: yes** (✅ ran the spike). Hailo's own
`voice_assistant` reference app was the tell: it loads Speech2Text and LLM on **one shared
VDevice and holds both resident for the process lifetime**. I reproduced that on BenderPi:
Whisper-Small + Qwen2.5-1.5B loaded together, interleaved STT→LLM→STT→LLM inference ran
cleanly, and STT latency with the LLM resident was identical to STT alone (0.75s). The
follow-up spike also mapped the *actual* constraint: the on-chip **KV-Cache is a
singleton** — adding the VLM alongside the LLM fails with
`HAILO_INVALID_OPERATION(6): KV-Cache is already in use`. Whisper doesn't use the
KV-Cache; LLM and VLM each need exclusive hold of it. So the resource model is simply:
**Whisper + (LLM ⊕ VLM), all resident, forever.**

Everything else follows from that. The per-turn release/reload ping-pong
(`stt.release()` → LLM `_load()` → `release_chip()` → Whisper reload) is ~11s of
self-imposed tax per AI turn (📊 `ai_hailo_load` median 8.4s, ✅ Whisper-Small load 2.5s),
built to serialize access that never needed serializing. And the second false constraint:
the code comment "Hailo doesn't expose token-level streaming" is **wrong for the installed
SDK** — `LLM.generate()` is a context manager that yields tokens (📖 vendor source,
✅ ran it: TTFT 0.38s trivial prompt, **1.1s with the full Bender system prompt**,
6.9 tok/s).

Combining the two: AI turns currently take a **measured median 14.5s to first sentence**
(📊 `ai_local_first_sentence_ms`, n=34) and ~15.5s to first audio. Resident models + token
streaming gets first audio to **~3.5–5s** — a 3–4× improvement on the worst number in the
system, with no new hardware, no new models, no cloud. That is recommendation #1 and #2,
and nothing else on the list comes close.

On the model side: the 5.3 zoo holds nothing better for the chat role (Qwen3-1.7B is 35%
slower — your rejection stands, ✅ verified 4.78 vs 7.35 TPS in the zoo table);
Whisper-Base is measurably 2× faster than Small on this device but STT is not the
bottleneck, so don't switch; the function-calling HEF can't sit beside the chat model
(KV-Cache singleton), which reshapes — mostly kills — that idea. The wake-word livekit
retrain plan is endorsed and now has a stronger evidence base: **June 2026 logged 477
sessions of which 451 were greeting-only** (📊 false-wake storm in the hey_jarvis era),
and even now most sessions have zero real turns.

---

## Ground truth established this session

### Spike 1 — HEF coexistence + streaming (✅ ran on BenderPi, service live and idle)

One VDevice, `group_id="SHARED"`, mirroring `hailo_apps/.../voice_assistant.py`:

| Step | Result |
|---|---|
| Whisper-Small load | 2.53s |
| transcribe #1 (Whisper alone) | 0.76s |
| Qwen2.5-1.5B load (Whisper resident) | 8.56s |
| `llm.generate()` stream, trivial prompt | TTFT 0.38s, 6.9 tok/s |
| **transcribe #2 (LLM resident)** | **0.75s — identical, no eviction** |
| generate #2 (no reload) | TTFT 0.37s |
| transcribe #3 | 0.75s |
| Clean release of all three objects | OK |

**Coexistence: PASS.** The `llm_warm_session` hardware gate is cleared — and the feature
as designed (session-scoped warmth) is the wrong scope; see rec #1.

### Spike 2 — three-way with VLM (✅ ran)

Whisper + Qwen2.5 loaded fine; adding `VLM(vdevice, Qwen2-VL-2B)` failed:
`Failed to acquire KV-Cache. KV-Cache is already in use by another model.`
This is the precise mechanism behind the "KV-Cache contention" that got `vlm_enabled`
turned off. It is not fixable by ordering or care: **LLM and VLM are mutually exclusive
while loaded.** Whisper is unaffected (no KV-Cache use).

### Spike 3 — TTFT with the real persona prompt (✅ ran)

Full `BENDER_SYSTEM_PROMPT` + user turn, warm model: **TTFT 1.10–1.11s** (two runs),
6.9 tok/s decode. First sentence of a typical reply ≈ 12–25 tokens ≈ **2.5–4.5s**, plus
~0.5–1.5s Piper for the first sentence → **first audio ~3.5–5s**.
(Bonus observation: one of two runs opened with "I'm sorry, but I can't assist with
that." — the first-sentence quality gate stays necessary in the streaming path.)

### Live metrics (📊 `/home/pi/bender/logs/metrics.jsonl` + `.1`, ~4.5 months)

| Metric | n | median | p90 | notes |
|---|---|---|---|---|
| `ai_hailo_load` (HEF reload tax) | 7 | 8,424ms | 8,642 | fires on **every** AI turn today |
| `ai_hailo_call` (generate_all) | 206 | 3,540ms | 25,620 | recent runs 4–10s |
| `ai_local_first_sentence_ms` | 34 | **14,563ms** | ~19,000 | the real user wait |
| `time_to_first_audio_ms` | 42 | 1,067ms | 2,496 | *after* first sentence — streaming plan **is shipped** |
| `turn_total` (AI turns since 6/15) | 35 | ~25–28s | — | includes playback |
| `stt_transcribe` whisper-small-hailo | 274 | 635ms | 2,214 | fine |
| `stt_transcribe` whisper-base-hailo | 131 | **314ms** | 539 | 2× faster, historical |
| `stt_transcribe` base.en CPU fallback | 837 | **19,817ms** | 75,256 | catastrophic degraded mode |
| `ai_api_call_stream` (Claude Haiku) | 99 | 3,412ms | 5,941 | cloud is currently ~5× faster to speak than local |
| `tts_generate` (per sentence) | 5,884 | 1,578ms | 4,054 | cache: 220 hits / 101 misses |
| `hailo_busy_lockout` / `hailo_lock_stuck` | 0 | — | — | zombie machinery has never fired |

### Usage reality (📊 conversation logs, 558 sessions / 655 logged turns)

- 552 of 655 turns are the wake greeting. Real user turns ≈ **103 in 4.5 months**.
- Of those, **76 (74%) are UNKNOWN → AI**. Handlers: weather 4, HA control 2, time 3,
  personal 3. **The AI path effectively is the product**; the intent/handler tier mostly
  serves the greeting.
- **477 of 567 session-ends had ≤1 logged turn** (greeting only). June alone: 477
  sessions, 451 greeting-only — ~15 false wakes/day in the hey_jarvis era. July (hey_bender
  v0.1): 6 sessions total — FP fixed, but recall 0.589 + sparse use means the device is
  barely being exercised.
- Local-vs-cloud routing outcomes: 60 local success, 13 hedge escalations, 2 ReadTimeout,
  1 too_short → ~79% local success.

### Corrections to the session brief (things you concluded that need amending)

1. **"Streaming plan unstarted" — wrong.** `2026-06-12-streaming-local-llm-tts.md` is
   fully shipped: `generate_stream()`, `ResponseStream`, `time_to_first_audio_ms` all
   exist in code and emit live metrics. The plan doc just never got a status header.
   What's *missing* is Hailo-side token streaming — today the Hailo path fakes it
   (generate_all → yield once), which is why first-sentence latency is 14.5s.
2. **"Hailo has no token API" (comment in `ai_local.py`) — wrong for HailoRT 5.3 /
   hailo-apps 25.12.** `LLM.generate()` streams; `save_context`/`load_context`/
   `set_stop_tokens`/`tokenize` also exist (📖 API surface inspected on-device).
3. **"Pi 5 (8GB)" — wrong: this Pi has 4GB** (✅ `free -h`: 4.0Gi total, ~2.4Gi
   available). Matters for the CPU fallbacks: Ollama Qwen-1.5B and faster-whisper in 4GB
   alongside everything else is why the CPU paths measure 20s+.
4. **Qwen3-1.7B / Qwen3-VL rejections — confirmed** (✅ zoo table: 4.78 / 4.74 TPS vs
   7.35 / 7.04). Right call, keep.
5. **Function-calling HEF idea — mostly dead on arrival** given the KV-Cache singleton;
   see rec #6 for the one viable shape.
6. **`llm_warm_session` — right instinct, wrong scope.** Session-scoped warmth still pays
   the 8.5s load on the first AI turn of every session, which given usage (1–2 turn
   sessions) is nearly every AI turn. The vendor pattern is process-lifetime residency.

### Hailo GenAI Zoo 5.3 inventory (✅ fetched `MODELS.rst`)

LLMs: DeepSeek-R1-Distill-Qwen-1.5B (7.96 TPS), **Llama3.2-1B-Instruct (9.89 TPS)**,
Qwen2-1.5B (8.06), **Qwen2.5-1.5B (7.35, deployed)**, Qwen2.5-Coder-1.5B (8.13),
Qwen3-1.7B (4.78). Function-calling: Qwen2-1.5B-FC-v1 (6.69). VLM: Qwen2-VL-2B (7.04,
on disk), Qwen3-VL-2B (4.74). STT: Whisper-Tiny (48 TPS) / Base (25) / Small (10.6, deployed).
No TTS, no embedding models, no wake-word models in the zoo.

---

## Recommendations, ranked

### 1. Hold Whisper + LLM resident for the process lifetime — delete the release ping-pong
**The change:** Adopt the vendor `voice_assistant` pattern. One module (e.g.
`scripts/hailo_hub.py`) owns the VDevice + `Speech2Text` + `LLM` singletons, created
lazily at first use (or warmed at startup in the existing `stt.warm_up` thread) and
released only at process exit. Delete: per-turn `stt.release()` (wake_converse.py:420),
per-turn `release_chip()` (session.py:247), `reset_hailo()` per turn, the
`_RELEASE_SETTLE_S` settle window, and the `llm_warm_session` flag (subsumed). Keep: the
`_infer_lock` non-blocking zombie guard around `generate` (it has never fired — 📊 zero
`hailo_busy_lockout`/`hailo_lock_stuck` in 4.5 months — but it's cheap insurance), the
init-failure cooldown, and `close()` at exit.

**Expected win (measured, not estimated):** −8.4s LLM HEF reload per AI turn
(📊 `ai_hailo_load`), −2.5s Whisper reload per post-AI turn (✅ spike; this one is
currently *invisible* — `_load_model()` runs untimed inside `listen_and_transcribe()`
before the mic even opens, so it also delays recording start). AI-turn first-sentence
median drops from 14.5s to ~4–6s even *without* streaming.

**Approach:** ~1 day. `stt.py` and `ai_local.py` both import the hub; their public APIs
don't change, so `session.py`/`wake_converse.py` changes are deletions. Ship behind
`hailo_resident: true` (default on) for one-config-edit rollback. Watch for:
`ai_hailo_load` should fire **once per process** after this lands — that's the
proof-it-worked metric, visible in STATUS.md with zero new instrumentation.

**Risk:** Long-run stability of a permanently-held VDevice is the one unknown (❓) — the
historical crashes that motivated the dance were later attributed to the
`__exit__`+`__del__` double-release bug, already fixed, but only a multi-week soak proves
it. Failure mode is bounded: process death releases the device (✅ observed clean release;
systemd restarts within seconds), and the existing 60s init-retry covers transient init
failures. The converse process holding the KV-Cache permanently means bender-web can never
load an LLM/VLM — it already doesn't (📖 `transcribe_file(prefer_cpu=True)` was built for
exactly this). **Cost to try: ~1 day + passive soak.**

### 2. Hailo token streaming: `llm.generate()` → sentence chunker → existing pipeline
**The change:** Give `_HailoLLMResponder` a real `generate_stream()` using
`with self._llm.generate(...) as gen`, feeding the same sentence-flush logic
`_OllamaResponder.generate_stream()` already has (extract the `_flush_sentence` helper —
it's duplicated today). `LocalAIResponder.generate_stream()` stops wrapping `generate_all`
as a fake one-item stream. First-sentence quality gate stays exactly where it is
(responder.py eagerly pulls sentence 1) — spike 3 showed Qwen still occasionally opens
with an out-of-character refusal, so the gate is load-bearing.

**Expected win:** With #1 in place: TTFT 1.1s (✅ measured with the real persona prompt) +
first sentence ~2.5–4.5s + Piper ~1s → **first audio ~3.5–5s vs 15.5s today**. Decode at
6.9 tok/s ≈ 3.4 words/s vs ~2.5–3 words/s speech rate, so subsequent sentences pipeline
just ahead of playback — the turn *feels* continuous. This also closes most of the gap to
cloud (Claude streaming first-audio ~2–3.5s 📊), which changes the routing calculus: local
stops being the slow option you tolerate for principle.

**Approach:** ~1 day incl. tests (the Ollama stream tests are a template). Details that
need care: (a) the lock — generation now spans playback time, so `_infer_lock` is held
longer; keep acquire-non-blocking + Ollama failover semantics unchanged. (b) abort — on
`audio.abort()` the generator must be closed (the `with` handles it, but verify a
mid-stream `GeneratorExit` doesn't wedge the LLM object — one on-device test). (c) strip
`<|im_end|>` per-token (vendor's `StreamingTextFilter` shows the pattern).
**Risk:** low — the ResponseStream/speak_from_iter/play_stream pipeline is proven with the
Ollama and Claude paths. **Cost: ~1 day.**

### 3. Wake word: run the livekit retrain now (plan already written — endorse, with data)
The 2026-07-29 plan is sound and Phase 0 already passed on-device. New evidence from this
session strengthens it: the June false-wake storm (451 greeting-only sessions in one
month) and the fact that **~84% of all sessions ever recorded ended with zero real turns**
mean wake quality — both directions — has wasted more of this system's runtime than any
other component. v0.1 fixed FP by giving up recall (0.589); livekit's published operating
point (86% recall / 0.08 FPPH) is a different curve, not a tradeoff shuffle.
**Win:** ~1.5× recall, ~3× fewer FPs (published numbers — treat as ranking until Phase 2's
same-harness eval). **Cost: ~$2 Modal + on-device retune** (start at threshold 0.68,
try `oww_frames_required: 1`). **Do this in parallel with #1/#2 — it's independent.**

### 4. Fix the catastrophic STT degraded mode: CPU fallback → tiny.en
**The change:** One config value: the CPU fallback model (`whisper_model`) from `base.en`
to `tiny.en`. **Why:** measured on this 4GB Pi, base.en CPU transcribe is median **19.8s,
p90 75s** (📊 n=837 — this path has run a *lot* historically), which also poisons the
session-timeout logic and feels like a dead assistant. tiny.en measured median 852ms
(📊 n=160). Accuracy loss is real but the comparison isn't tiny-vs-base, it's
tiny-vs-unusable: this path only runs when Hailo STT is already down. With #1, the
fallback becomes rarer still. **Cost: minutes. Risk: none worth naming.**

### 5. Keep Whisper-Small as the resident STT — don't switch to Base
Contrary to "a latency dial nobody has measured": both are measured on this device
(📊 table above). Base is ~320ms faster per utterance, but STT at 635ms median is ~4% of
an AI turn, and transcript quality gates everything downstream (intent, HA matching, LLM
input). Spike 1 shows no memory pressure holding Small + Qwen. **Revisit only if** a
future resident set needs the headroom. **Cost of my recommendation: zero — it's the
status quo, now with numbers.**

### 6. Function-calling HEF: skip — with one recorded exception
The KV-Cache singleton kills the attractive version (FC model resident *alongside* the
chat model for HA routing): every intent-parse would cost an 8.5s swap each way. The only
viable shape is **replacing** Qwen2.5-1.5B with Qwen2-1.5B-FC-v1 as the *sole* resident
LLM doing both persona chat and tool calls (−9% TPS, and it's a Qwen2-generation model —
persona quality regression likely). Usage data says don't: HA_CONTROL fired **2 times in
4.5 months**. The keyword classifier is not the constraint on this system. Record the
swap-shape for the future (if voice-driven HA ever becomes a real habit, it's a
one-model-swap experiment, ~an afternoon), and move on.

### 7. VLM / vision: leave off; if scene context is ever wanted, don't route it through the KV-Cache
`vlm_enabled: false` is now explained mechanically, not superstitiously: VLM can never be
resident with the LLM. Options if vision returns: (a) accept a ~17s LLM↔VLM swap per
scene analysis — fine for explicit "what do you see?" turns, terrible for ambient
injection; (b) use the non-GenAI path for ambient context — `yolov8m.hef` (on disk) runs
on the standard inference API, and the IMX500 camera does its own on-sensor inference,
neither touches the KV-Cache (📖/❓ — yolo coexistence with a resident LLM is very likely
but untested; 10-minute spike if it matters). Current usage (vision off, nobody asking)
says: **defer entirely.**

### 8. Simplify the AI fallback ladder: demote Ollama to a config option, not a chain link
With #1+#2, the resident Hailo LLM answers in ~4s or fails fast; the current
auto-fallback to CPU Ollama (median 8–25s on a 4GB Pi, competing with everything for RAM)
delivers a *worse* experience than the existing cloud escalation or an in-character
canned error. Suggested ladder: Hailo → (hybrid: cloud | local_only: canned error).
Keep Ollama reachable via `ai_backend` config for the genuinely-Hailo-dead scenario, but
stop paying its complexity in the hot path (`local_llm_timeout` clamp interactions, the
double history implementation, warm-up thread). **Win:** less code in the most complex
file, faster failure. **Risk:** offline-first purists lose the "no-Hailo, no-cloud, still
answers" tier — but that tier measurably takes 20s+ and nobody has used it on purpose.
**Cost: ~half a day. Priority: low — do it opportunistically when touching `ai_local.py`
for #2.**

### 9. Response priority chain: keep the shape, shrink the choreography
The chain itself (clip → pre-gen → promoted → handler → AI) is cheap, testable, and
correct for the greeting-heavy traffic. What *is* accidental complexity is the per-turn
hardware choreography threaded through the loop — `stt.release()`, `reset_hailo()`,
`release_chip(warm=...)`, settle windows — all deleted by #1. Two micro-cleanups while
there: `will_need_thinking()` runs `intent.classify()` and then `get_response()` runs it
again (double classify per turn — cache it); and the static-tier docs oversell the chain
("response priority chain" implies the tiers matter — 74% of real turns go straight to
AI). Not worth a refactor on their own.

---

## What to do first

| Order | Item | Cost | Win |
|---|---|---|---|
| 1 | Resident Whisper+LLM (#1) | ~1 day + soak | −11s/AI turn, measured |
| 2 | Hailo token streaming (#2) | ~1 day | first audio 15.5s → ~4s |
| 3 | livekit retrain (#3) — parallel track | ~$2 + retune | recall 0.59 → ~0.86 (published) |
| 4 | tiny.en CPU fallback (#4) | minutes | degraded mode 20s → ~1s |
| — | FC-HEF (#6), VLM (#7), Whisper-Base (#5) | — | distractions; skip |
| opportunistic | Ollama demotion (#8), double-classify (#9) | hours | code health |

The proof plan for #1+#2 is already built into the existing instrumentation: after
deploy, `ai_hailo_load` should appear once per process instead of once per turn,
`ai_local_first_sentence_ms` should drop from ~14.5s to ~3–5s, and
`time_to_first_audio_ms` stays where it is. STATUS.md will show all three without any
new code.

**Confidence summary:** coexistence, KV-Cache exclusivity, TTFT, tok/s, STT-by-model
latencies, usage/false-wake stats — all measured this session (✅/📊). The main inference
(❓) is long-run stability of permanently-resident models: vendor-pattern-backed, spike-
verified for minutes, but only a soak proves weeks. Ship #1 behind its config flag and
let the existing watchdog/STATUS.md machinery do what it was built for.
