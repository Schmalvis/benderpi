# Checkpoint: Picovoice Free Tier Sunset

**Date:** 2026-05-22  
**Type:** Forced migration — external dependency end-of-life  
**Status:** Migration complete. Porcupine is fully removed; BenderPi runs on
openWakeWord with a trained `hey_bender_v0.1.onnx` model (see
`docs/superpowers/plans/2026-06-12-openwakeword-migration.md` for the
implementation plan that was actually executed, and
`docs/superpowers/plans/2026-06-12-hey-bender-wake-word.md` for the custom
model training that followed). `scripts/hey-bender.ppn` (the old Porcupine
model) has been deleted from the repo.

---

## What Happened

Picovoice announced the permanent Free Tier for Porcupine (wake word detection) will be disabled on **June 30, 2026**. Existing Free Tier AccessKeys stop working on that date. New users get a 7-day trial only; continued use requires a paid plan.

BenderPi has used Porcupine since day one for "Hey Bender" wake word detection via a custom `.ppn` model file (`scripts/hey-bender.ppn`). The `PORCUPINE_ACCESS_KEY` in `.env` is a Free Tier key — it will stop working on the deadline.

---

## Impact

Without action, BenderPi will fail to start its wake word loop after June 30, 2026.

Affected components:
- `scripts/wake_converse.py` — Porcupine init + detection loop
- `scripts/config.py` — `porcupine_access_key` field
- `requirements.txt` — `pvporcupine==4.0.2`, `pvrecorder==1.2.7`
- `.env.example` — `PORCUPINE_ACCESS_KEY`

---

## Decision

Migrate to **openWakeWord** — free, open-source, ONNX-based, no API key, fully offline.  
Rationale: aligns with the offline-first design philosophy; avoids any future vendor dependency.

Migration scope (initial, superseded): `docs/superpowers/plans/2026-05-22-openwakeword-migration.md`
Migration implementation (authoritative, executed): `docs/superpowers/plans/2026-06-12-openwakeword-migration.md`

---

## Project State at This Checkpoint

- VLM rewrite complete (Qwen2-VL-2B on Hailo, 2026-05-14)
- Audio resilience plan complete (2026-05-14)
- Web UI fully operational
- All three response modes (converse / clips / TTS) functional
- BenderPi running stable on Pi 5 with Hailo AI HAT+

---

## Deadline

**June 30, 2026** — existing Free Tier AccessKeys disabled.
