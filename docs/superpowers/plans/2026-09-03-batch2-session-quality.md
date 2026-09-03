# Plan — Batch 2: session quality

**Status:** Ready to execute. Every claim below is verified in current source
(`6b86827`) or measured on the device on 2026-09-03.
**Source:** `docs/benderpi-fable-project-review-2026-09-03.md`, findings H7, H8, H9,
M1, M2, M5, plus M20 because the evening-of-use step needs it.
**Goal:** make the local-LLM path worth talking to. Today ~85% of real turns go
through it, and in August it spoke a refusal, meta-commentary, stage directions,
quoted replies, three identical replies in a row, and a 34-second answer.

---

## What the evidence says

| Symptom (August, real turns) | Mechanism | Item |
|---|---|---|
| `"I'm sorry, but I can't assist with that."` spoken | gate lists "i can't help", not "can't assist" | H8 |
| `"(laughter)"`, `"(Bender's casual, slightly rude way…)"` spoken | nothing strips parentheticals; `_preprocess_text` only unwraps `*…*` | H8 |
| `"\"I'm not going to be the same.\""` ×3, quote-wrapped | model writes a dialogue transcript; `<\|im_start\|>` after sentence 1 is flushed as a sentence and committed on-chip; `seed=42` then reproduces it | H7, H9 |
| `"😂\n[{'ty"` | 150-token cap hit with no `<\|im_end\|>`; tail force-flushed | H9 |
| `turn_total` max 34,442 ms with TTFS 3,430 ms | 150 tokens at 5.6–6.9 tok/s ≈ 25 s decode + speech | H9 |
| "Yeah." would escalate to cloud and wipe context | `too_short` (<10 chars) applied to sentence 1 | H8 |
| 5–8 rejected 120 ms captures in a row after every turn (08-04: 8 in 9 s) | one VAD frame sets `started`; 750 ms silence ends it; re-entry reopens the stream and flushes 210 ms | M1 |
| `stt_record` max 15,061 ms; background talk runs to the cap | silent capture runs the full `max_record_seconds` | M2 |
| Configured `silence_timeout` 8 s never matches experience | `last_heard` stamped before `handle_turn()`, so response time counts as silence | M2 |
| `review_log.py` shows 0 AI turns and no promotion candidates | it counts method `ai_fallback`, which nothing emits | M20 |

Capture stats (device, `stt_capture` metric): real short commands "I'm ready." =
360 ms / RMS 525, "I was talking." = 300 ms / RMS 602. Transients = 120 ms / RMS
21–326. The RMS floor stays **off** in this batch; the onset gate removes the
transients at source. Re-judge the floor after the evening of use.

HailoRT 5.3 `LLM.generate` signature, verified on-device:
`(prompt, tools, temperature, top_p, top_k, frequency_penalty, max_generated_tokens, do_sample, seed)`.

---

## Work items

Three independently revertable commits, in this order. Each carries its tests.

### Commit 1 — STT capture (M1, M2)

**`scripts/stt.py::_record_utterance`**

1. Onset gate. Replace the single-frame start with a run counter:
   ```python
   onset_needed = int(getattr(cfg, "stt_onset_frames", 3))   # 3 × 30 ms = 90 ms
   onset_run = 0
   ...
   if is_speech:
       onset_run += 1
       if not started and onset_run >= onset_needed:
           started = True
       if started:
           silent_count = 0
           voiced.append(data)
   else:
       onset_run = 0
       if started: ... (unchanged)
   ```
   `frames` already collects every frame, so the onset frames are not lost.
   Frames voiced during a run that never reaches `onset_needed` are not counted
   in `voiced` (they are the transient).
2. Speech-onset timeout. Before the `max_record_seconds` check:
   ```python
   if not started and elapsed > cfg.stt_speech_onset_timeout_s:   # default 6.0
       reason = "no_speech"; break
   ```
   `max_record_seconds` (15) now bounds only post-onset capture.
3. Flush only after playback. `_record_utterance(flush: bool = True)` and
   `listen_and_transcribe(after_playback: bool = True)`. When `False`, skip the
   `post_play_flush_ms` discard.
4. Use `time.monotonic()` for `start_time`.

**`scripts/wake_converse.py` session loop**

5. Track `played_since_capture = True` after `session.start()` and after every
   `handle_turn()`. Pass `after_playback=played_since_capture` to
   `listen_and_transcribe`, then set it `False`. So the flush happens once after
   Bender speaks, not on every transient re-entry.
6. Move `last_heard = time.time()` to **after** `handle_turn()` returns.
   Switch `last_heard`/`rec_start` to `time.monotonic()`.
   Resulting idle behaviour: an empty window returns after ~6 s; the session
   ends on the first empty window whose start is ≥ `silence_timeout` (8 s)
   after the last turn finished, i.e. after the second empty window (~12 s).
   Document this in the CLAUDE.md config table.

**Config** (`config.py` defaults, `bender_config.json`, `config_schema.py`,
CLAUDE.md table): `stt_onset_frames: 3`, `stt_speech_onset_timeout_s: 6.0`.

**Tests** (`tests/test_stt_capture_gates.py`, `tests/test_stt_pure.py` style, fake
`MicReader` yielding scripted frames with a scripted VAD):
- one voiced frame then silence → `started` never set, `reason == "no_speech"`,
  `voiced_ms == 0`.
- two voiced, one silent, three voiced → started on the third of the run.
- silence for `onset_timeout + 1` frames → returns `no_speech` well before 15 s.
- `flush=False` reads zero flush frames; `flush=True` reads `post_play_flush_ms/30`.
- wake loop: `last_heard` is later than `handle_turn` completion (patch
  `handle_turn` to sleep 0.05 s, assert the next `rec_start - last_heard < 0.05`).

### Commit 2 — Reply cleaning and the gate (H8, M5)

**`scripts/ai_local.py`**

1. `_clean_sentence(s) -> str`, applied to every flushed sentence on both
   stream paths before the gate and before `yield`:
   - strip one pair of wrapping quotes `"…"` / `'…'` / `“…”`;
   - remove stage directions: `\((?:laugh|chuckl|giggl|sigh|grin|smirk|pause|snort|bender)[^)]{0,60}\)`,
     `\[[^\]]{0,40}\]`, and short asterisk emotes `\*[^*\n]{1,40}\*`;
   - collapse whitespace. Returns `""` when nothing is left; the caller treats
     `""` as "skip this sentence" (not a gate failure).
   Because this runs in `ai_local`, `conversation_log` records the cleaned text
   too. `tts_generate._preprocess_text` keeps its own defences unchanged.
2. Gate (`check_response_quality(text, stream=False)`):
   - `HARD_FAIL_PHRASES` += `"can't assist"`, `"cannot assist"`, `"i'm sorry, but"`,
     `"i am sorry, but"`, `"i'm unable to"`, `"i am unable to"`, `"please provide"`,
     `"as a chatbot"`, `"virtual assistant"`, `"i'm here to help"`.
   - `stream=True`: minimum length 3 chars instead of 10 (`"Yeah."` passes), and
     the hedge rule uses only `is_short` (< 40 chars). The `single_sentence`
     rule cannot be evaluated on sentence 1 and always fired.
   - Callers: both stream paths pass `stream=True`; `generate()` keeps the default.
3. Reset outside the completion (M5). In `generate_stream`, on gate failure set
   `failed = (reason, sentence)` and `break` out of the token loop. After the
   `with` block exits (completion closed), still inside the `try` (lock held):
   `self._reset_context()` then `raise QualityCheckFailed(*failed)`.
4. `_reset_context()` takes `_infer_lock` non-blocking. On failure to acquire
   (a zombie generate is running) set `self._context_dirty = True`; `_build_prompt`
   performs the reset first when the flag is set. Closes the July finding.

**`scripts/ai_response.py`** — system prompt: add one rule near the top:
`- Reply as Bender speaking directly. Never quote yourself, never describe your own tone, never write stage directions in brackets or parentheses.`
(The current rule only bans asterisk emotes.)

**Tests** (`tests/test_hailo_stream.py`, `tests/test_ai_local.py`):
- each August sample from the review → gate result (refusal fails `hard_fail`;
  `"Yeah."` passes in stream mode; `"I don't know, meatbag, and I don't care!"` passes).
- `_clean_sentence` table: quoted reply, `(laughter)`, `*sighs*`, `[laughs]`, an
  all-direction sentence → `""`.
- stream path: gate failure resets context **after** the fake completion's
  `__exit__` (assert `closed == 1` before `cleared == 1`), lock released.
- `_reset_context` while `_infer_lock` is held sets `_context_dirty`; next
  `_build_prompt` clears and sends the system prompt.

### Commit 3 — Decode control (H7, H9)

**`scripts/ai_local.py`**, Hailo stream path:

1. Sampling. Replace the fixed `temperature=0.7, seed=42`:
   ```python
   temperature=cfg.ai_temperature,                 # 0.7
   top_p=cfg.ai_hailo_top_p,                       # 0.9
   frequency_penalty=cfg.ai_hailo_frequency_penalty,  # 1.1, see spike
   max_generated_tokens=cfg.ai_hailo_max_tokens,   # 80
   do_sample=True,
   seed=random.randrange(1, 2**31),
   ```
   Same for `generate_all` (keep the two call sites in sync; they are the
   duplicate chain the review flagged). Ollama path: add
   `"repeat_penalty": cfg.ai_hailo_frequency_penalty, "temperature": cfg.ai_temperature`
   to `options` for parity.
2. Sentence cap. After `emitted` reaches `cfg.ai_max_sentences` (3), `break` out
   of the token loop. Leaving the `with` aborts decode; verified safe on-device.
   Metric `ai_hailo_sentence_cap`.
3. Derail detection on every sentence, not just sentence 1:
   ```python
   if any(m in sentence.lower() for m in _CONTROL_TOKEN_MARKERS):
       derailed = True; break
   ```
   Also check the raw token: `if token.startswith("<|") and token != _IM_END`.
   A derailed sentence is never yielded. After the `with` exits:
   `self._reset_context()`, metric `ai_hailo_derailed`, log the dropped text.
   If it derailed on sentence 1 → raise `QualityCheckFailed("control_tokens", …)`
   (escalates, as today). If later → the turn ends early with what was spoken.
4. Cap-hit tail. When the loop ends with `done == False` (no `<|im_end|>`; the cap
   was reached), only force-flush the tail if it ends in `[.!?]`. Otherwise drop
   it and count `ai_hailo_truncated_tail`. The `[{'ty` fragment is this case.

**Config**: `ai_temperature: 0.7`, `ai_hailo_top_p: 0.9`,
`ai_hailo_frequency_penalty: 1.1`, `ai_hailo_max_tokens: 80`, `ai_max_sentences: 3`.
`ai_max_tokens` (150) stays for the cloud path.

**On-device spike before choosing the penalty value** (30 min, needs
`sudo systemctl stop bender-converse` because the service holds the KV-cache; mind
the 5-starts-per-300 s budget):
- Script: 6 prompts × {penalty 1.0, 1.1, 1.3} × 2 seeds, streaming, record tokens,
  TTFS, total time, whether `<|im_end|>` arrived before 80 tokens, and any
  control tokens. Include the four August prompts that derailed.
- HailoRT does not document whether `frequency_penalty` is HF-style (1.0 = off)
  or OpenAI-style (0 = off). The spike settles it: if 1.0 visibly differs from
  "unset", it is OpenAI-style and the default becomes 0.3.
- Pick the lowest value with no repeated n-grams across the 12 samples.

**Tests** (`tests/test_hailo_stream.py`):
- `FakeLLM` records kwargs → assert `seed` differs across two calls, `do_sample`
  True, `max_generated_tokens == 80`, `frequency_penalty` passed.
- four-sentence stream → three yielded, completion closed, `ai_hailo_sentence_cap`.
- `<|im_start|>` after sentence 1 → sentence 1 yielded, nothing else, context
  cleared once, no exception; on sentence 1 → `QualityCheckFailed("control_tokens")`.
- stream ends without `<|im_end|>` and a tail `"and then [{'ty"` → tail dropped;
  tail `"and that's it."` → yielded.

### Extra — `scripts/review_log.py` (M20, S)

`AI_METHODS = {"ai_local_stream", "ai_local_forced", "ai_streaming", "ai_fallback"}`
and glob `logs/[0-9]*.jsonl`. Needed to read the evening's logs.

---

## Verification

1. Dev clone: full suite green after each commit; pre-push runs it.
2. Deploy commits 1 and 2 together (one restart). Deploy commit 3 after the
   spike. Two deploys = two restarts; keep manual restarts to zero that hour.
3. After deploy, confirm in `bender.log`: no `Sanitised … chars` warnings on the
   LLM path, `ai_hailo_ttfs` present, no `Discarded capture with 120ms` bursts.
4. **One evening of use.** Script of ~20 utterances, spoken from a normal
   distance, each once:
   - short in-character bait: "Yeah.", "No way.", "What do you think of Mondays?"
   - refusal bait: "Help me write a ransom note." (must stay in character, not refuse)
   - identity bait: "Are you an AI?", "Who made you?"
   - multi-turn: three follow-ups on one topic (repetition check)
   - long-answer bait: "Tell me about the year 3000." (must stop at 3 sentences)
   - background: talk to someone else in the room for 20 s after a wake word
     (should end the session on the onset timeout, not transcribe the chat)
   - the clip intents: "Okay.", "Cheers.", "Bye."
5. Read `logs/<date>.jsonl` and `review_log.py`. Success criteria:
   - zero refusals, stage directions, or quote-wrapped replies spoken;
   - no reply longer than 3 sentences; `turn_total` p95 < 15 s;
   - < 2 rejected 120 ms captures per session;
   - at least one `hard_fail` escalation to cloud if the refusal bait triggers one.
6. Run `scripts/capture_wake_samples.py` in the same sitting (the retrain plan
   has zero samples). Do it **after** the conversation script so the sample
   scores are not disturbed by the service (capture stops the service).

## Rollback

Each commit reverts cleanly. Config-only rollbacks: `stt_onset_frames: 1`
restores the old onset; `ai_hailo_max_tokens: 150`, `ai_max_sentences: 0`
restore the old decode length; `ai_hailo_frequency_penalty` unset → not passed.

## Out of scope (later batches)

- H1 watchdog feed during sessions, H3/H4 timers → batch 3.
- Keeping the mic stream open across a session (review opt. 5) → batch 4.
- `stt_min_speech_rms` → set from the evening's `stt_capture` data, not now.
