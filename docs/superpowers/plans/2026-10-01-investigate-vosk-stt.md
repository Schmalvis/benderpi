# Side note — investigate Vosk as an STT engine

**Status:** NOT STARTED. Raised by the owner 2026-10-01 as a side investigation.
**Do not start this before the Halloween blockers.** It is a latency idea, and
the Halloween review findings are safety ones.

---

## Why it could matter here

The current path is Whisper-Small on the Hailo NPU, and it is **not streaming**:
`stt.listen_and_transcribe()` records a complete utterance, waits for the VAD to
say the speaker stopped, then transcribes the whole clip in one shot.

Measured `stt_transcribe` on the device, most recent turns:

| | ms |
|---|---|
| best | 508 |
| typical | 950–2200 |
| worst seen | 4551 |

That whole figure is dead air **after** the child stops talking and before
Bender can even begin thinking. It sits in front of the local LLM's 1.0–3.5s to
first sentence, so it is a straight addition to every turn.

**Vosk (Kaldi, alphacephei.com/vosk) is a streaming recogniser.** It emits
partial hypotheses while the person is still speaking. That is the interesting
property, not the accuracy: if partials are usable, the LLM prompt can be built
from a partial and the turn can start before the speaker finishes. On a good
case that hides the entire transcription cost.

Accuracy is the thing to be sceptical about. Whisper-Small is a much stronger
model than `vosk-model-small-en-us` (~40MB). The honest question is not "is
Vosk better" — it is "is Vosk fast enough, and accurate enough, that starting
earlier wins more than the extra errors cost".

## What is already known, from the 2026-09-28 engine research

- Licence: Apache-2.0. Models are permissive. Self-hosted, no account.
- Last release checked: v0.3.50, April 2024. Not dead, not busy.
- Model sizes: ~40MB small English, ~1.8GB large.
- It runs on CPU. The Hailo NPU does not help it, so it **costs CPU** on a
  4GB Pi 5 that already runs resident Whisper + Qwen, Piper, and uvicorn.
  Freeing the Hailo Whisper slot is itself worth something — the KV-cache is a
  singleton, so Whisper leaving the chip is the only way the VLM could ever be
  resident beside the LLM.
- Rhasspy does not offer Vosk as a wake word, and the earlier review scored its
  keyword-spotting as a fallback only. **This note is about STT, not wake word.**

## The questions to answer, in order

1. **Does a partial hypothesis arrive early enough to be useful?** Measure the
   time from speech onset to a partial that contains the final words. If that
   is not materially better than 950ms, stop — there is no reason to continue.
2. **Word error rate on this device's own audio.** We have 109 positives, 150
   hard negatives and 30 minutes of household audio in `data/wake_samples/`,
   plus real conversation logs with Whisper's own transcripts to compare
   against. That is a ready-made benchmark through the right microphone.
3. **CPU cost while the rest of the stack runs.** Measure with `bender-converse`
   live, not on an idle box. The mic reader starves above `mic_read_timeout_s`
   (10s) under load, and CLAUDE.md records a real incident where CPU saturation
   faked a mic stall.
4. **Does it change the architecture for the better?** If Vosk is good enough,
   Whisper leaves the Hailo chip. Price what that unlocks (the VLM) and what it
   costs (accuracy on the long questions the assistant roadmap needs, like
   "what is the lifecycle of a butterfly").

## How to try it cheaply

Nothing needs to touch the live service. `stt.py` already has two backends
behind one interface (Hailo and CPU faster-whisper), so a third is a contained
change. Before writing any of it:

```bash
# on the device, service stopped
venv/bin/pip install vosk
# score Vosk against Whisper on audio we already have, offline
```

The comparison set should be the clips in `data/wake_samples/` plus any
recorded call audio from `scripts/capture_background.py`, because both are real
far-field audio through the XVF3800 — which is the only audio that predicts
this device's behaviour.

## Decision to make after step 1

If partials are fast and accurate enough: Vosk becomes a candidate for the
Halloween/conversation path, where latency dominates. Whisper stays for anything
where accuracy matters more than speed. Two backends chosen per scenario is
already the shape `stt.py` uses.

If partials are slow or noisy: record the measurement and close this. The
alternative latency lever, already proven, is to answer instantly with a real
clip while the model works.
