# Plan — Retrain "hey bender" with real-voice positives

**Status:** Ready to execute. Capture tooling built and tested; nothing recorded yet.
**Supersedes as next action:** `2026-07-29-livekit-wakeword-retrain.md` (see "Why not livekit first").

---

## The problem, measured

On 2026-08-01 the wake word had not fired once in 34 hours of uptime, and took
many attempts to trigger even after the threshold was lowered.

| Audio | Peak score |
|---|---|
| Synthetic "hey bender" (Piper) | **0.9684** |
| Live human voice, best case | **0.227 – 0.272** |
| Live human voice, typical | **0.001 – 0.002** |
| Loud non-wake audio (RMS 11363) | 0.001 |
| Peak over 34h of background | 0.023 |
| The one successful trigger | **0.948** |

Two conclusions, and the second is the one that matters:

1. **Specificity is excellent.** Nothing that wasn't the wake word has ever
   exceeded 0.023. There have been **zero** false wakes.
2. **The model is bimodal, not under-confident.** It either recognises the
   phrase almost perfectly (0.948) or doesn't see it at all (0.002). It is not
   sitting just under the threshold waiting for a nudge — which is why lowering
   `oww_threshold` 0.35 → 0.10 bought far less than expected.

**Root cause, confirmed in the training code:** `train_hey_bender.py` generates
every positive sample synthetically via `piper-sample-generator` and
`en-us-libritts-high.pt`. The model has never heard a real person say the
phrase. The pipeline had already loosened `target_false_positives_per_hour` to
buy recall, and it wasn't enough, because the deficit isn't in the
recall/precision trade-off — it's that the training distribution doesn't
contain the speakers who use the device.

---

## Why not livekit first

`2026-07-29-livekit-wakeword-retrain.md` is built on a **100× false-positive
reduction**. We now have data saying false positives are not our problem: zero
false wakes in 34 hours, background peaking at 0.023 against a 0.10 threshold.
Spending the larger effort to fix the metric that is already healthy, while the
broken one goes untouched, is the wrong order.

Livekit stays on the roadmap, re-justified on **recall** rather than FP rate,
and reassessed after this retrain. If real-voice positives fix recall, it may
not be needed at all.

---

## Why capture on the device

Non-negotiable: samples are recorded through Bender's own hardware.

The failure is a domain gap between synthetic and real audio. Recording on a
laptop or phone would close that gap and open another — different microphone,
different preamp, different room. The specific path that must be represented in
training is:

- ReSpeaker 2-Mic HAT, WM8960 codec
- **29dB** input boost + **12dB** capture gain, ALC off (measured 2026-08-01)
- `mic_shared` dsnoop device, stereo→mono ttable downmix, 16kHz
- the actual room, with its actual reverb and noise floor

`scripts/capture_wake_samples.py` records through exactly this path. Because
`mic_shared` is dsnoop, capture works alongside a running service — but the
script stops `bender-converse` by default anyway, since Bender waking
mid-session would talk over the recordings and flip the codec to 44100Hz.

---

## Phase 1 — Capture (~45 min of your time, no cost)

```bash
# on the Pi
cd /home/pi/bender

# one run per person in the household
venv/bin/python scripts/capture_wake_samples.py --speaker martin
venv/bin/python scripts/capture_wake_samples.py --speaker <other>

# phonetically-similar phrases that must NOT wake him
venv/bin/python scripts/capture_wake_samples.py --speaker martin --mode hard_negative

# household background: TV, cooking, conversation — no wake word
venv/bin/python scripts/capture_wake_samples.py --mode ambient --minutes 20
```

**Targets:** ~100 positives per speaker (10 conditions × 10), ~90 hard
negatives, ~20 min ambient.

**Capture resumes.** Stop whenever you like with `q` (or Ctrl-C in ambient
mode) and re-run the identical command later. Progress lives on disk, not in
the process — the tool counts the clips already recorded per condition, skips
finished conditions, and continues numbering from where it stopped. Nothing is
overwritten. Doing this in five ten-minute sittings is equivalent to one long
one, and is probably better: voices tire, and a tired voice is not the voice
the model needs to recognise.

The 10 conditions span the generalisation envelope rather than piling up
identical clips — distance (0.5m / 1.5m / 3m), level (quiet / normal / raised),
rate (fast / drawn out), off-axis, over background noise, and walking past.
Breadth is what's missing; count is not.

**The tool gives live feedback per clip:** peak level (flagging clipping or
too-quiet), and the score from the *currently deployed* model with a
`would wake` / `MISSED` verdict. At the end it prints the current model's recall
over everything captured. **That number is the before-baseline the retrain has
to beat** — it costs nothing extra because the clips are being scored anyway.

Clips are trimmed to the voiced span and centred in a 2.0s window. A recording
where VAD finds no speech is **dropped, not padded** — a silent "positive"
would teach the model the wake word sounds like nothing, which is the exact
failure being fixed.

Output is gitignored (`data/wake_samples/`). These are recordings of the
household and the repo is public.

---

## Phase 2 — Training integration (the one unresolved piece)

**Open question, resolve before running:** how `train_hey_bender.py` should mix
real clips into a set that piper-sample-generator otherwise produces wholesale.
Two candidate integration points, to be confirmed against the openWakeWord
training config rather than assumed:

- **(a)** Drop real clips into the generated-positives directory before the
  augmentation stage, so they receive the same RIR and background-noise
  augmentation as synthetic ones. Preferred if the config allows it — ~200 real
  clips augmented becomes a meaningfully sized real-voice population.
- **(b)** Pass them as a separate positive source if the config exposes one.

**Mixing ratio matters.** With `n_samples=25000` synthetic, 200 real clips are
0.8% of the set and will be drowned. Options: oversample the real clips
(duplicate before augmentation) to ~10–20% of positives, or cut the synthetic
count. Start at ~15% real and treat the ratio as the primary tuning knob if
Phase 4 is marginal.

The captured ambient audio and hard negatives feed the negative side, holding
the false-positive rate down as recall rises — the two move together, which is
why Phase 1 captures negatives at all.

---

## Phase 3 — Train

```bash
modal run scripts/train_hey_bender.py --n-samples 25000 --steps 50000
```

Cost is a single Modal GPU run, in line with previous runs (see
`2026-06-12-hey-bender-wake-word.md`).

Output ships as **`hey_bender_v0.2.onnx`**, alongside v0.1 rather than over it,
so rollback is a one-line `oww_model_path` change.

---

## Phase 4 — Validate before deploying

**Hold out ~20% of the real clips from training** and never let the run see
them. This is the whole experiment: a model scored only on data it trained on
tells you nothing about the gap that caused this.

```bash
# held-out real clips, scored with the candidate model
venv/bin/python scripts/wake_score.py --wav data/wake_samples/holdout/<clip>.wav

# harness self-check — should still be ~0.97
venv/bin/python scripts/wake_score.py --synthetic
```

**Ship criteria:**

| Metric | Target | v0.1 baseline |
|---|---|---|
| Recall on held-out real clips at threshold 0.35 | **≥ 90%** | ~0% |
| Peak score on ambient negatives | **< 0.1** | 0.023 |
| Peak score on hard negatives | **< 0.35** | not measured |

Recall is measured at **0.35**, not the current 0.10. If v0.2 needs a threshold
of 0.10 to hit 90%, it hasn't actually fixed the generalisation gap — it's
leaning on the same margin erosion v0.1 was. Restoring 0.35 is part of success.

---

## Phase 5 — Deploy + confirm in the wild

1. `oww_model_path` → `models/hey_bender_v0.2.onnx`, `oww_threshold` → `0.35`.
2. Push; auto-deploy handles it (unit files aren't involved).
3. Use it normally for a day.
4. Confirm from the journal: `Wake word detected` events appearing at natural
   usage rates, and `Wake idle: peak score` staying low when nobody is speaking.

**Rollback** is a config edit back to v0.1 + 0.10. Keep v0.1 on the device.

---

## Risks

1. **Real clips get drowned by synthetic ones.** Most likely failure. Mitigated
   by the mixing ratio in Phase 2; it's the first thing to change if Phase 4 is
   marginal.
2. **Recall rises and false positives rise with it.** That's why hard negatives
   and ambient audio are captured in Phase 1 rather than as an afterthought.
   Phase 4 measures both directions before anything ships.
3. **Overfitting to one speaker.** Capture every household voice, and hold out
   by *speaker* as well as by clip where there's enough data.
4. **The device sounds different in six months** (mic moved, room changed, gain
   reset). `wake_score.py --record` re-measures in a minute; the ALSA gains are
   recorded above so drift is detectable.
5. **StartLimitBurst.** The capture script stops and starts `bender-converse`
   once per run. Several runs back-to-back plus a deploy can exhaust 5 starts /
   300s — check `systemctl show bender-converse -p Result` for `start-limit-hit`
   before suspecting the code.

---

## Not doing

- **Retraining from scratch on real audio only.** ~200 clips is nowhere near
  enough; the synthetic set is still doing the heavy lifting for coverage.
- **Changing the wake phrase.** Unrelated to the defect.
- **Lowering the threshold further.** Already established as the wrong lever:
  the model scores 0.948 or 0.002, so there's almost nothing in between to
  reclaim.
