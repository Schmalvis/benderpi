# Plan — Halloween autonomous Bender (30 days), and the road after it

**Written:** 2026-10-01. **Hard deadline:** evening of 2026-10-31.
**Supersedes as the active priority:** everything wake-word
(`2026-09-24-wake-word-retrain-v0.2.md`). See "Why the wake word is parked".

---

## The aims, as stated by the owner

**Long term.** An Alexa-class assistant: ask a question, get an answer from the
local LLM or a cloud one when needed, with web search for things like "what is
the lifecycle of a butterfly", rewritten in Bender's voice and spoken. Control
and query Home Assistant devices and sensors. Weather. Use the embedded camera
for awareness of *who* he is talking to — Martin, Jenn, Lincoln, an adult, a
child. Latency as low as possible throughout.

**This month.** Bender sits out front on Halloween with candy in his chest
cavity, as in previous years. This year he should **converse with children**.
Today that needs a human puppeteer behind the web dashboard. The aim is a
genuine unscripted interaction with nobody driving it.

---

## Why the wake word is parked

A trick-or-treater will not say "hey bender". They will walk up and say
"trick or treat". So the wake word is not on the Halloween path at all, and
five training runs of work on it does not advance this deadline.

State it is left in: **v0.1 deployed at threshold 0.10**, near-field recall
25%, zero false wakes measured over 29.3 minutes of household audio. The
better model (v0.3_r35, 92% near-field) is on disk and **not** deployed: at
0.10 it fires 4.09 times/hour on recorded speech, which interrupted the
owner's work calls on 2026-09-30. `v0.3 at threshold 0.35` gives 50%
near-field recall with zero false wakes on that audio and is the candidate,
pending confirmation on the call recording now being captured
(`scripts/capture_background.py`).

That decision is independent of Halloween and can wait until November.

---

## What Halloween actually needs

Four things, in dependency order. Nothing here needs the wake word.

### 1. A trigger that is not a wake word

**Open-mic mode.** In Halloween mode the device skips the wake word entirely:
any speech near it that clears the existing capture gates starts a turn. The
machinery exists — `stt.listen_and_transcribe()` already does VAD onset
(`stt_onset_frames`), a minimum voiced length (`stt_min_speech_ms`) and an
onset timeout (`stt_speech_onset_timeout_s`).

This is right for a doorstep: he *should* answer whoever is standing there.
The risk is the street triggering him, which is what `stt_min_speech_rms`
exists for and has never been set. The call recording will give the data to
set it.

**Camera trigger, if time allows.** The IMX500 runs detection on its own
silicon, so person detection does not touch the Hailo NPU and does not
conflict with the LLM's KV-cache (the reason `vlm_enabled` is false). A
person-in-frame signal would let him greet *before* anyone speaks, which is
the difference between answering and being alive. Treat as a stretch goal; the
open mic is the deadline-safe path.

### 2. Latency a child will tolerate

Measured today, per turn: `time_to_first_audio_ms` 1.0–3.5 s, `turn_total`
7.4–19.0 s. A child will have walked away.

The fix is not a faster model, it is **not making them wait for the model**:

- **Instant acknowledgement.** The moment speech ends, play a real Bender clip
  from the 88 already on disk. He is talking in <200 ms while the LLM thinks.
  This single change hides almost all of the LLM latency.
- **A Halloween response bank.** Pre-built WAVs via `prebuild_responses.py`
  plus intent patterns for the handful of things that will actually be said:
  "trick or treat", "can I have some candy", "nice costume", "are you real",
  "what are you". Zero latency, no LLM, no surprises.
- **Harder caps for this mode.** `ai_hailo_max_tokens` 80 → ~48 and
  `ai_max_sentences` 3 → 2. Measured decode is 5.6–6.9 tok/s, so 48 tokens is
  ~7 s of decode and two short sentences is ~6 s of speech.
- **Budget:** first audio < 300 ms, complete reply < 6 s. Both measurable from
  existing metrics, so this is a gate, not a hope.

### 3. Content that is safe for children, unattended

The current system prompt permits "occasional mild profanity (damn, hell)" and
"rude, dismissive, self-aggrandising". That is correct for the household and
wrong for other people's children on a doorstep with no adult supervising the
device.

Halloween mode needs its own prompt: in character, cheeky, **no profanity, no
insults aimed at the listener, nothing frightening a small child**, one or two
sentences. Plus a stricter quality gate than the household one, and **no cloud
escalation in this mode** — a failed local reply plays a canned line instead of
sending a child's words to an API.

### 4. Robust enough to leave alone for three hours

- The device power window is 07:00–22:00; trick-or-treating is ~17:00–20:30, so
  that is fine, but it must not be the thing that fails.
- Mains power and network out front. **Open question for the owner.**
- The XVF3800 cold-boot corruption recovers itself now, but it is worth a
  deliberate restart and a `scripts/eval_wake_model.py --skip-mic-check`-style
  capture check an hour before go-live.
- A kill switch: the dashboard's existing puppet-only toggle stops autonomy
  instantly, and the owner can take over by typing.

---

## The dashboard, which is the fallback and the safety net

The puppet path costs a **full service stop and restart per utterance**:
`service_guard` stops `bender-converse`, plays, then restarts — and that
restart reloads two Hailo models (~3.0 s Whisper + ~9.7 s Qwen measured). That
is unusable for live puppeting.

Two fixes, cheapest first:

1. **Hold puppet-only mode for the whole evening.** The toggle already exists
   (`/api/actions/mode`), and in that mode the service is already stopped, so
   each utterance pays no restart. Needs measuring, and the dashboard should
   show plainly which mode is live.
2. **Speak without stopping the service.** The wake loop already watches IPC
   files (`session_file`, `abort_file`, `end_session_file`). A "say this" file
   would let the dashboard inject speech into the *running* process, so puppet
   and autonomy coexist and there is no restart at all. This is the better
   answer and the bigger change.

Also worth measuring end-to-end: button press → sound out of the speaker,
including camera-stream contention, since the live feed and playback compete
for the same device.

---

## Sequencing

| # | Work | Why this order | Effort |
|---|---|---|---|
| 1 | Open-mic Halloween mode behind a config flag | Nothing else can be tested without a trigger | M |
| 2 | Instant acknowledgement clip | Biggest perceived-latency win, independent of everything | S |
| 3 | Halloween response bank + intents | Removes the LLM from the common cases | S |
| 4 | Kid-safe prompt, stricter gate, no cloud in this mode | Must land before any unattended run | M |
| 5 | Puppet without a restart (IPC "say this") | Makes the fallback usable and is reusable later | M |
| 6 | Latency gates in the metrics + a go-live checklist | So "it feels fast" becomes a number | S |
| 7 | One evening of real testing with the family | The only honest test before strangers | — |
| 8 | Camera person-detect greeting | Stretch; cut first if time runs short | L |

Items 1–4 are the minimum for autonomous operation. 5 is the minimum for a
comfortable fallback. If everything slips, the fallback is this year's
arrangement: puppeting by hand, which already works.

---

## After Halloween — the assistant roadmap

Recorded now so this month's choices do not paint us into a corner.

- **Web search with answer synthesis.** "Lifecycle of a butterfly" needs a
  search tool, a summariser and the Bender rewrite. The streaming
  sentence pipeline already in place is the right shape for it: speak sentence
  one while sentence two is still being written.
- **Home Assistant queries, not just commands.** Device and sensor *status*
  ("is the back door shut", "how warm is the office") on top of today's
  control-only `ha_control.py`.
- **Who am I talking to.** Face or voice identity for Martin / Jenn / Lincoln,
  and adult vs child. The adult/child distinction is also a **safety** input:
  it should pick the prompt, not just the greeting. Note the hardware
  constraint — the Hailo KV-cache is a singleton, so the VLM cannot be resident
  beside the LLM; identity work should live on the IMX500 or on CPU.
- **Latency everywhere.** The same two levers: answer instantly with something
  real, and keep the model's share of the turn short.

---

## Answers from the owner (2026-10-01), and what they settle

1. **Mains power and reliable wifi out front.** So the cloud is *available* —
   but Halloween mode still stays local-only, because answer 4 says a canned
   "no idea what you're talking about" is acceptable. If silence is survivable,
   sending a child's speech to an API to avoid it is a bad trade.
2. **Owner always at home while Bender is out front.** So the dashboard kill
   switch is always within reach, and the failure mode is embarrassment rather
   than an unsupervised machine. This is what makes autonomy acceptable at all.
3. **Possibly several children talking at once.** Nobody can design that away.
   What follows from it: **one reply per detected utterance, and a cooldown**
   so turns cannot stack while three of them shout. Short replies help here
   twice — less to talk over, and less to interrupt.
4. **Worst acceptable failure is silence, or "I have no idea what you're
   talking about".** This is the most useful answer of the five. It means the
   fallback does not have to be clever, so the gate can be *strict*: anything
   the local model produces that fails the check becomes a canned line, and
   nothing escalates to the cloud. Cheap, private, fast, and safe.
5. **Children will be within 2 m.** Near-field, which is exactly where the mic
   and the model work. The far-field problem does not block Halloween, which
   is another reason the wake-word work can wait.

### What those answers change in the design

- **No cloud in Halloween mode.** Not a privacy nicety: answer 4 removes the
  only reason to want it.
- **A cooldown between turns** (`halloween_cooldown_s`), because of answer 3.
  Without it, three children produce three overlapping sessions.
- **The gate can be strict.** A rejected reply plays a canned line, so
  rejecting too much costs charm, not function.
- **Near-field gates only** for the mic tuning; no far-field work this month.
