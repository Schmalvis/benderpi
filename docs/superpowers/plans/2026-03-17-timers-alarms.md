# BenderPi Timers & Alarms — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan.

**Goal:** Add voice-controlled named timers and alarms with Bender-style alert behaviour, play-pause dismissal, web UI, and persistence.

**Architecture:** New `timers.py` (CRUD + persistence), `time_parser.py` (NLP), `timer_handler.py` (Bender responses). Alert mode in `wake_converse.py` with play-pause mic/speaker cycling. Web API for timer management.

**Spec:** `docs/superpowers/specs/2026-03-17-timers-alarms-design.md`

---

## Tasks

1. time_parser.py + tests
2. timers.py + tests
3. Intent patterns + tests
4. timer_handler.py + responder integration
5. Alert mode in wake_converse.py + LED flash + prebuild alert clips
6. Web API endpoints + dashboard timer UI
7. Config, .gitignore, docs
