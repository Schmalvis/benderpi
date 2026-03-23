#!/usr/bin/env python3
"""
latency_bench.py — BenderPi pipeline latency benchmarking.

Tests each stage of the response pipeline in isolation and reports
p50/p95 timings. Safe to run while bender-converse is active (uses
shared Hailo VDevice group).

Usage:
    cd /home/pi/bender
    venv/bin/python scripts/latency_bench.py [--runs N] [--no-ai] [--no-tts]
"""

import argparse
import os
import sys
import time
import statistics
import json
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from dotenv import dotenv_values
_env = dotenv_values(os.path.join(BASE_DIR, ".env"))
os.environ.update({k: v for k, v in _env.items() if v})


# ── helpers ────────────────────────────────────────────────────────────────

def _fmt(ms):
    if ms is None:
        return "     N/A"
    return f"{ms:7.0f}ms"

def _pct(samples, p):
    if not samples:
        return None
    s = sorted(samples)
    idx = max(0, int(len(s) * p / 100) - 1)
    return s[idx]

def _run(label, fn, runs, *, quiet=False):
    times = []
    errors = []
    if not quiet:
        print(f"  {label} ", end="", flush=True)
    for _ in range(runs):
        t0 = time.monotonic()
        try:
            fn()
            times.append((time.monotonic() - t0) * 1000)
            if not quiet:
                print(".", end="", flush=True)
        except Exception as e:
            errors.append(str(e))
            if not quiet:
                print("x", end="", flush=True)
    if not quiet:
        print()
    return times, errors

def _row(label, times, errors):
    if times:
        p50 = statistics.median(times)
        p95 = _pct(times, 95)
        mn, mx = min(times), max(times)
        err = f"{len(errors)}" if errors else "-"
        return f"  {label:<28} {_fmt(p50)} {_fmt(p95)} {_fmt(mn)} {_fmt(mx)}  {err}"
    else:
        status = f"{len(errors)} errors" if errors else "skipped"
        return f"  {label:<28} {'':>8} {'':>8} {'':>8} {'':>8}  {status}"


# ── main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BenderPi pipeline latency benchmark")
    parser.add_argument("--runs", type=int, default=5, help="Repetitions per stage (default: 5)")
    parser.add_argument("--no-ai", action="store_true", help="Skip LLM stages")
    parser.add_argument("--no-tts", action="store_true", help="Skip TTS stage")
    args = parser.parse_args()

    print(f"\nBenderPi Latency Benchmark  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print(f"Runs per stage: {args.runs}\n")

    results = {}
    medians = {}

    # ── Stage 1: STT ────────────────────────────────────────────────────
    print("[1] STT — Whisper transcription (file mode)")
    import stt

    # Pick a short existing wav as test input (real speech, no mic needed)
    candidate_wavs = [
        os.path.join(BASE_DIR, "speech/wav/yesss.wav"),
        os.path.join(BASE_DIR, "speech/wav/imbender.wav"),
        os.path.join(BASE_DIR, "speech/wav/areyoukidding.wav"),
    ]
    test_wav = next((w for w in candidate_wavs if os.path.exists(w)), None)

    if test_wav:
        print(f"  Using: {os.path.basename(test_wav)}")
        # Warm up (first call initialises the model)
        print(f"  Warming up STT model...", end="", flush=True)
        try:
            stt.transcribe_file(test_wav)
            print(" ready")
        except Exception as e:
            print(f" failed: {e}")

        times, errors = _run("transcribe_file ×" + str(args.runs),
                             lambda: stt.transcribe_file(test_wav), args.runs)
        results["STT (file)"] = (times, errors)
        medians["stt"] = statistics.median(times) if times else 0
    else:
        print("  No test wav found — skipping")
        results["STT (file)"] = ([], ["no test wav"])
        medians["stt"] = 0

    # ── Stage 2: Intent classification ──────────────────────────────────
    print("\n[2] Intent classification")
    import intent as intent_mod

    test_queries = [
        "what time is it",
        "tell me a joke",
        "what's the weather like",
        "turn off the living room lights",
        "who invented electricity",
        "set a timer for five minutes",
    ]
    intent_times = []
    for q in test_queries:
        t0 = time.monotonic()
        intent_mod.classify(q)
        intent_times.append((time.monotonic() - t0) * 1000)
    print(f"  Classified {len(test_queries)} test phrases")
    results["Intent classify"] = (intent_times, [])
    medians["intent"] = statistics.median(intent_times)

    # ── Stage 3: Local LLM ──────────────────────────────────────────────
    if not args.no_ai:
        print("\n[3] LLM — local AI (Hailo → Ollama fallback)")
        from ai_local import LocalAIResponder

        ai = LocalAIResponder()
        # Short prompt to minimise token generation noise
        prompt = "Reply in exactly four words."

        print(f"  Warming up LLM...", end="", flush=True)
        try:
            ai.generate(prompt)
            print(" ready")
        except Exception as e:
            print(f" failed: {e}")

        times, errors = _run(f"generate ×{args.runs}", lambda: ai.generate(prompt), args.runs)
        results["LLM (local)"] = (times, errors)
        medians["llm"] = statistics.median(times) if times else 0
    else:
        results["LLM (local)"] = ([], [])
        medians["llm"] = 0

    # ── Stage 4: TTS ────────────────────────────────────────────────────
    if not args.no_tts:
        print("\n[4] TTS — Piper synthesis")
        import tts_generate

        test_phrases = [
            ("short (1 sentence)", "Bite my shiny metal ass!"),
            ("medium (2 sentences)", "I'm Bender, baby. Please kill me."),
            ("long (3 sentences)", "All I know is that I know nothing. Except for the fact that I know that. And also that I'm great."),
        ]

        all_tts_times = []
        all_tts_errors = []
        for label, phrase in test_phrases:
            runs_this = max(1, args.runs // len(test_phrases))
            times, errors = _run(f"{label} ×{runs_this}", lambda p=phrase: _tts_and_cleanup(tts_generate, p), runs_this)
            results[f"TTS ({label})"] = (times, errors)
            all_tts_times.extend(times)
            all_tts_errors.extend(errors)
        medians["tts"] = statistics.median(all_tts_times) if all_tts_times else 0
    else:
        medians["tts"] = 0

    # ── Report ──────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  RESULTS")
    print("=" * 70)
    print(f"  {'Stage':<28} {'p50':>8} {'p95':>8} {'min':>8} {'max':>8}  err")
    print(f"  {'-'*28} {'-'*8} {'-'*8} {'-'*8} {'-'*8}  ---")

    for label, (times, errors) in results.items():
        print(_row(label, times, errors))

    # E2E estimate
    e2e = medians["stt"] + medians["intent"] + medians["llm"] + medians["tts"]
    print()
    print(f"  {'Estimated E2E (AI turn)':<28} {_fmt(e2e)}")
    print(f"  (STT {medians['stt']:.0f} + intent {medians['intent']:.0f} + LLM {medians['llm']:.0f} + TTS {medians['tts']:.0f} ms)")
    print(f"  Note: excludes mic recording time and audio playback duration.")
    print("=" * 70)

    # Write result to logs
    _save_result(results, medians, e2e)
    print()


def _tts_and_cleanup(tts_generate, phrase):
    wav = tts_generate.speak(phrase)
    os.unlink(wav)


def _save_result(results, medians, e2e_ms):
    out_path = os.path.join(BASE_DIR, "logs", "latency_bench.jsonl")
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "medians_ms": {k: round(v, 1) for k, v in medians.items()},
        "estimated_e2e_ms": round(e2e_ms, 1),
        "stages": {
            label: {
                "p50": round(statistics.median(times), 1) if times else None,
                "p95": round(_pct(times, 95), 1) if times else None,
                "min": round(min(times), 1) if times else None,
                "max": round(max(times), 1) if times else None,
                "n": len(times),
                "errors": len(errors),
            }
            for label, (times, errors) in results.items()
        },
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"\n  Results saved → logs/latency_bench.jsonl")


if __name__ == "__main__":
    main()
