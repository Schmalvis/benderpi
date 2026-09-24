# Plan — Retrain "hey bender" on real-voice positives (v0.2)

**Status:** Ready to execute. Data captured 2026-09-22/23; the integration
question that blocked `2026-08-01-wake-word-real-voice-retrain.md` is resolved
below against the upstream training code.
**Supersedes:** `2026-08-01-wake-word-real-voice-retrain.md` (its Phase 1 is done).

---

## The measured baseline

Captured through Bender's own mic (XVF3800, `mic_shared`, clean stream), speaker
`martin`, scored against the deployed `hey_bender_v0.1.onnx` at threshold 0.10:

| Set | Size | v0.1 behaviour |
|---|---|---|
| Positives | 100 clips, 10 conditions | **wakes on 6 (6%)** |
| Hard negatives | 90 clips, 9 phrases | **false-wakes on 22 (24%)** |
| Ambient household sound | 20 min | **0 frames over threshold**, peak 0.002 |

Per condition (positives), "would wake" out of 10:

| close | mid | far | quiet | loud | fast | slow | off-axis | background | moving |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 | **3** | **2** | **1** | 0 |

Per phrase (hard negatives), false wakes out of 10:

| hey vendor | hey bender's | hey bend | hey Brenda | okay then | hey there / hey friend / bender / play defender |
|---|---|---|---|---|---|
| **7** (median 0.68) | **7** (median 0.70) | **5** | 2 | 1 | 0 |

**What this says, and it is not what the August note said.** v0.1 is not deaf to
real voices. It fires *confidently* on "hey vendor" while scoring the real phrase
at 0.001. It is not keying on the phrase at all — it keys on a phonetic/cadence
shape learned from `piper-sample-generator`'s synthetic positives, which is why
only the drawn-out "heeey benderrr" reads as a positive. The August conclusion
"false positives are zero" holds **only for ordinary household sound**; against
deliberate near-misses the false-wake rate is 24%.

---

## Decisions taken (2026-09-24)

1. **Drop `hey bender's` from the negative set.** It contains the wake phrase.
   Training it as a negative teaches the model to suppress a legitimate wake.
   Its 10 clips stay on disk, excluded from training, and are scored at
   validation as a *watch* metric only (a wake there is acceptable behaviour).
2. **One speaker for v0.2.** A second speaker is a later, optional improvement,
   not a blocker. Consequence: the speaker-generalisation risk is not measured
   by this run — recorded as an open risk, not a gate.
3. **Weight towards real clips.** Real positives are the fix; synthetic ones
   only supply phonetic coverage. Concrete target is the real fraction of
   `positive_train`, swept at **10% / 20% / 35%** (§5).

---

## How real clips actually enter training (was the open question)

Read from `openwakeword/train.py` (upstream main, fetched 2026-09-24):

- `--generate_clips` writes WAVs into
  `<output_dir>/<model_name>/{positive,negative}_{train,test}/`.
- It counts what is already there:
  `n_current = len(os.listdir(positive_train)); generate n_samples - n_current`
  — **so pre-placed real clips both inject themselves and reduce the synthetic
  count by the same number.** The ratio needs no new config key.
- `--augment_clips` globs those directories and multiplies the file list by
  `augmentation_rounds`, then runs RIR + background augmentation and feature
  extraction. Real clips therefore get the *same* augmentation as synthetic
  ones, and each duplicate copy receives independent augmentation.
- `total_length` is derived from the median positive_test duration, with a
  32000-sample (2.0 s) floor. Our clips are exactly 2.0 s, 16 kHz, mono, 16-bit
  — no resampling or padding needed.
- `background_paths` + `background_paths_duplication_rate` control what gets
  mixed in as background during augmentation.

So integration is **option (a)** from the August plan, confirmed rather than
assumed: copy files in before the generate stage. Oversampling is `k` numbered
copies of each real clip.

---

## 1. Splits (fixed before any training, written to a manifest)

| Set | Train | Held out | Rule |
|---|---|---|---|
| Positives | 80 | **20** | 2 per condition, so every condition is represented on both sides |
| Hard negatives | 64 | **16** | 2 per phrase × 8 phrases (`hey bender's` excluded entirely) |
| Ambient | 15 min | **5 min** | held-out minutes never used as augmentation background |

The held-out clips are the whole experiment. A model scored on clips it trained
on says nothing about the gap that caused this. Split by file, seeded, and
recorded in `data/wake_samples/split.json` so every run and every later
comparison uses the identical division.

---

## 2. Getting 49 MB from the Pi to Modal

`modal.Volume`, uploaded once from the dev clone:

```bash
rsync -a pi@BenderPi.local:/home/pi/bender/data/wake_samples/ /tmp/wake_samples/
modal volume create bender-wake-samples
modal volume put bender-wake-samples /tmp/wake_samples /
```

Mounted read-only in the training function. Not committed to git — the
household's audio never enters the repo (`data/wake_samples/` is gitignored, and
`tests/test_capture_wake_samples.py::test_captures_are_gitignored` pins that).

---

## 3. Code changes to `scripts/train_hey_bender.py`

All additive; the existing synthetic-only path stays reachable by omitting the
new flags.

| Change | Detail |
|---|---|
| `--real-samples-volume` | Modal volume name; absent = today's synthetic-only behaviour |
| `--real-positive-fraction` | default `0.20`; share of `n_samples` filled with real clips |
| `--real-negative-copies` | default `25`; copies of each real hard negative into `negative_train` |
| `_seed_real_clips()` | after `_clone_repos`, before `--generate_clips`: read `split.json`, copy train-side positives `k = round(n_samples * fraction / n_train)` times into `positive_train/`, hard negatives into `negative_train/`, as `real_<condition>_<nnn>_<copy>.wav` |
| ambient as background | append the 15 held-in ambient minutes to `background_paths` with `background_paths_duplication_rate` 3, so augmentation hears the actual room, not only FMA music |
| `--output-name` | already parameterised; ship as `hey_bender_v0.2.onnx` **alongside** v0.1 |
| assert | fail loudly if the volume is mounted but `split.json` is missing, rather than silently training synthetic-only |

Held-out clips are never copied into any training directory. That is asserted in
code, not left to the caller.

---

## 4. Validation harness — `scripts/eval_wake_model.py` (new)

Runs on the **device** (the model must be judged through the same audio path it
will serve), takes a model path, scores every held-out clip, prints one table:

```
venv/bin/python scripts/eval_wake_model.py --model models/hey_bender_v0.2.onnx
```

Reports, at thresholds 0.10 / 0.35 / 0.50:

- recall on the 20 held-out positives, and per condition;
- false-wake rate on the 16 held-out hard negatives;
- frames over threshold across the 5 held-out ambient minutes (as false wakes
  per hour);
- `hey bender's` score, reported but not gated;
- the synthetic control (`--synthetic`), which must stay ~0.97 — if it collapses,
  the harness or the export is broken, not the model.

The same command against `models/hey_bender_v0.1.onnx` prints the baseline
column, so every comparison is like-for-like.

---

## 5. The sweep — ratio is the primary knob

Three runs, identical except the real fraction:

```bash
modal run scripts/train_hey_bender.py --n-samples 20000 --steps 50000 \
    --real-samples-volume bender-wake-samples --real-positive-fraction 0.10 \
    --output-name hey_bender_v0.2_r10.onnx
# ... 0.20 → _r20.onnx, 0.35 → _r35.onnx
```

At 20% and `n_samples=20000`: 4000 real slots ÷ 80 clips = **50 copies each**.
That is the tension in this plan — 80 unique recordings stretched across 4000
augmented instances. 10% under-weights the real voice (the v0.1 failure mode);
35% risks memorising 80 recordings. The sweep decides it with the held-out set
rather than by argument.

**Cost:** ~2–3.5 h wall clock per run on a Modal T4 at ≈$0.59/h, so **~$1.20–2.10
each, ~$4–6 for all three**. Runs are unattended and can go in parallel.

---

## 6. Ship criteria

Measured on held-out data only, with `eval_wake_model.py`:

| Metric | Gate | v0.1 |
|---|---|---|
| Recall on held-out positives @ 0.35 | **≥ 80%** | 6% @ 0.10 |
| Recall on the 6 "normal speech" conditions @ 0.35 | **≥ 75%** | 0% |
| False wakes on held-out hard negatives @ 0.35 | **≤ 10%** | 24% @ 0.10 |
| False wakes on held-out ambient | **0 per hour** | 0 |
| Synthetic control | ~0.97 | 0.97 |

Recall is gated at **0.35**, not today's 0.10. A model that needs 0.10 to pass
has not closed the generalisation gap; it is leaning on the same eroded margin
v0.1 leans on. Restoring 0.35 is part of the definition of success.

If no run passes: the fallback is more data (second speaker, more clips per
condition), not a lower threshold and not more synthetic samples.

---

## 7. Deploy

1. `scripts/deploy_hey_bender.sh` takes the model filename as `$1`
   (currently hardcoded to v0.1) and defaults to it.
2. On the device: `bash scripts/deploy_hey_bender.sh hey_bender_v0.2.onnx`.
3. Set `oww_threshold` 0.10 → **0.35** in the same commit as `oww_model_path`,
   and update the long `oww_threshold` note in CLAUDE.md, which documents the
   0.35 → 0.10 workaround this retrain is meant to retire.
4. Push; auto-deploy restarts the service. Keep `hey_bender_v0.1.onnx` on the
   device — rollback is two config values.
5. One day of normal use, then read the journal: `Wake word detected` at natural
   rates, `Wake idle: peak score` low when the room is quiet, and no
   `wake_mic_corrupt` noise confusing the picture.

---

## Risks

1. **80 unique clips, 4000 augmented copies.** The central risk. Held-out
   evaluation is the detector; the sweep is the mitigation; more real clips is
   the cure.
2. **Single speaker.** v0.2 may work well for one voice and poorly for others.
   Accepted deliberately (decision 2). Not gated, but re-test with a second
   voice before calling the wake word "fixed" for the household.
3. **Recall and false wakes move together.** "hey vendor" at 24% is the thing
   that gets worse if recall is bought carelessly, which is why hard negatives
   are trained on and gated, not just observed.
4. **Room drift.** The samples encode this room, this mic, these ALSA gains. If
   the device moves, `wake_score.py --record 20` re-measures in a minute.
5. **StartLimitBurst.** Deploy plus any device-side stop/start can exhaust 5
   starts / 300 s. Check `systemctl show bender-converse -p Result` for
   `start-limit-hit` before suspecting the model.
6. **Cold-boot mic corruption.** Now auto-recovered (3 mornings running), but a
   corrupt stream reads 0.001 on everything and would look exactly like a failed
   retrain. `eval_wake_model.py` prints the capture zero-fraction first for this
   reason.

---

## Not doing

- **Training on real audio only.** 80 clips is nowhere near coverage; synthetic
  samples still carry phonetic variety.
- **Lowering the threshold again.** Established as the wrong lever.
- **livekit-wakeword.** Re-judge after v0.2, on recall. Its stated benefit
  (100× fewer false positives) addresses a metric that is already 0/hour on
  household sound.
- **Changing the wake phrase.**
