#!/usr/bin/env python3
"""
Log review utility — summarises conversation logs and highlights
AI fallback queries worth promoting to static responses.

Usage:
    python3 scripts/review_log.py            # last 7 days
    python3 scripts/review_log.py --days 30  # last 30 days
    python3 scripts/review_log.py --all      # all logs
"""

import argparse
import collections
import glob
import json
import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR  = os.path.join(BASE_DIR, "logs")


def load_turns(days: int | None) -> list[dict]:
    pattern = os.path.join(LOG_DIR, "*.jsonl")
    files = sorted(glob.glob(pattern))
    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        files = [f for f in files if os.path.basename(f) >= cutoff + ".jsonl"]
    turns = []
    for path in files:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if rec.get("type") == "turn":
                    turns.append(rec)
    return turns


def summarise(turns: list[dict]):
    if not turns:
        print("No turns found.")
        return

    # Method breakdown
    method_counts = collections.Counter(t["method"] for t in turns)
    intent_counts = collections.Counter(t["intent"] for t in turns)
    ai_turns      = [t for t in turns if t["method"] == "ai_fallback"]
    error_turns   = [t for t in turns if t["method"] == "error_fallback"]

    total = len(turns)
    local = total - len(ai_turns) - len(error_turns)

    print(f"\n{'='*60}")
    print(f"  Bender Conversation Log Review")
    print(f"{'='*60}")
    print(f"  Total turns  : {total}")
    print(f"  Local (no API): {local}  ({100*local//total}%)")
    print(f"  AI fallback  : {len(ai_turns)}  ({100*len(ai_turns)//total if total else 0}%)")
    if error_turns:
        print(f"  Errors       : {len(error_turns)}")

    print(f"\n--- Response methods ---")
    for method, count in method_counts.most_common():
        bar = "#" * (count * 30 // max(method_counts.values()))
        print(f"  {method:<20s} {count:4d}  {bar}")

    print(f"\n--- Intent breakdown ---")
    for intent, count in intent_counts.most_common():
        print(f"  {intent:<20s} {count:4d}")

    if ai_turns:
        print(f"\n--- AI fallback queries (consider promoting frequent ones) ---")
        query_counts = collections.Counter(t["user_text"].lower().strip() for t in ai_turns)
        print(f"  {'Count':<6} Query")
        print(f"  {'─'*50}")
        for query, count in query_counts.most_common(20):
            flag = "  *** PROMOTE?" if count >= 3 else ""
            print(f"  {count:<6} {query}{flag}")

    if error_turns:
        print(f"\n--- Error fallbacks ---")
        for t in error_turns[-5:]:
            print(f"  [{t.get('ts','')}] {t.get('user_text','')}")
            print(f"    Response: {t.get('response_text','')}")

    print(f"\n--- To promote a frequent AI response to static ---")
    print(f"  1. Add entry to PROMOTED_RESPONSES in scripts/prebuild_responses.py")
    print(f"  2. Run: python3 scripts/prebuild_responses.py")
    print(f"  3. The response will be matched before the AI is called next time.")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    days = None if args.all else args.days
    turns = load_turns(days)
    label = "all time" if days is None else f"last {days} days"
    print(f"Loading turns ({label}) from {LOG_DIR} ...")
    summarise(turns)


if __name__ == "__main__":
    main()
