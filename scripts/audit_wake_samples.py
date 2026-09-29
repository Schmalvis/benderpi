#!/usr/bin/env python3
"""Transcribe captured clips and quarantine the ones that do not say the phrase.

WHY THIS EXISTS
---------------
2026-09-28: 115 of 130 "positive" clips did not contain "hey bender". The
capture prompts described the recording CONDITION but never printed the words,
so the speaker read the condition aloud -- close_normal_002 is "This is my
normal speaking voice", far_normal_001 is "3 metres away". openWakeWord labels
by directory with no transcript, so those trained as the wake word, copied
50-88 times each. Two GPU runs and a day of analysis rested on it.

The capture script now verifies each clip as it is recorded. This tool applies
the same check to clips captured before that existed, so the existing set can
be salvaged rather than thrown away wholesale.

Clips that fail move to data/wake_samples/quarantine/<mode>/ -- moved, not
deleted, because Whisper on a 2s far-field clip is not reliable enough to
destroy a recording on its own say-so.

POSITIVES ONLY, deliberately. The hard negatives were prompted with their
words printed from the first session, and the phrase matcher is generous by
design -- it accepts "vender"/"vendor" as a mangled "bender", so auditing
negatives with it would quarantine perfectly good "hey vendor" clips. Check
those by ear if you doubt them.

Usage (on the device; stop bender-converse first so Whisper is free):
    venv/bin/python scripts/audit_wake_samples.py              # report only
    venv/bin/python scripts/audit_wake_samples.py --apply      # move failures
"""

import argparse
import collections
import json
import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))

OUT_ROOT = os.path.join(BASE_DIR, "data", "wake_samples")
QUARANTINE = os.path.join(OUT_ROOT, "quarantine")


def _clips(mode: str) -> "list[str]":
    root = os.path.join(OUT_ROOT, mode)
    out = []
    for dirpath, _, names in os.walk(root):
        if QUARANTINE in dirpath:
            continue
        out += [os.path.join(dirpath, n) for n in sorted(names) if n.endswith(".wav")]
    return sorted(out)


def audit(mode: str, transcribe) -> "list[tuple]":
    """(path, transcript, verdict) per clip: ok / unclear / wrong.

    Only "wrong" is quarantined. Whisper mangles a 2s far-field "hey bender"
    into "Hey Pender" / "A feather" / "Ebato", so treating anything it fails
    to recognise as a bad clip would destroy most of a good set -- it would
    have quarantined 84 of 116 deliberately-recorded clips.
    """
    import capture_wake_samples as cap

    return [(path, t, cap.classify_clip(t))
            for path, t in ((p, transcribe(p)) for p in _clips(mode))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="move failing clips to quarantine/ (default: report only)")
    ap.add_argument("--json", help="write the full transcript list here")
    args = ap.parse_args()

    import capture_wake_samples as cap
    transcribe = cap._transcriber()
    if transcribe is None:
        raise SystemExit("no transcriber available — run this on the device, "
                         "with bender-converse stopped")

    mode = "positive"
    print(f"Transcribing {mode} clips (this takes a second each) ...\n")
    rows = audit(mode, transcribe)
    if not rows:
        raise SystemExit(f"no {mode} clips found under {OUT_ROOT}")

    by_group = collections.OrderedDict()
    for path, text, verdict in rows:
        g = os.path.basename(path).rsplit("_", 1)[0]
        by_group.setdefault(g, []).append((path, text, verdict))

    print(f"{'group':18s}  ok unclear wrong   what the wrong ones say")
    for g, items in by_group.items():
        n = collections.Counter(v for *_, v in items)
        bad = [t for _, t, v in items if v == "wrong"][:2]
        print(f"{g:18s} {n['ok']:3d} {n['unclear']:7d} {n['wrong']:5d}   "
              + " | ".join(repr(t[:34]) for t in bad))

    tot = collections.Counter(v for *_, v in rows)
    print(f"\n{tot['ok']} ok, {tot['unclear']} unclear (kept — the transcriber "
          f"cannot resolve a 2s far-field clip), {tot['wrong']} wrong.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump([{"path": p, "text": t, "verdict": v} for p, t, v in rows],
                      f, indent=2)
        print(f"Wrote {args.json}")

    failures = [p for p, _, v in rows if v == "wrong"]
    if not failures:
        print("Nothing to quarantine.")
        return
    if not args.apply:
        print(f"\n{len(failures)} clips would move to quarantine/. "
              f"Re-run with --apply to move them.")
        print("Then re-run scripts/split_wake_samples.py --force, because the "
              "split still lists the removed clips.")
        return

    for path in failures:
        rel = os.path.relpath(path, OUT_ROOT)
        dest = os.path.join(QUARANTINE, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(path, dest)
    print(f"\nMoved {len(failures)} clips to {QUARANTINE}/ (moved, not deleted).")
    print("Now re-run: venv/bin/python scripts/split_wake_samples.py --force")


if __name__ == "__main__":
    main()
