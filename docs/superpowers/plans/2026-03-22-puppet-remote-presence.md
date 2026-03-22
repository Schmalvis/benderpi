# Puppet Mode: Remote Presence Enhancement

**Added:** 2026-03-22
**Type:** Feature enhancement

---

## Problem

Puppet mode currently lets Martin speak *as* Bender remotely, but it is one-way:
Martin has no awareness of what is happening in the room around Bender — who is
there, what they are saying, how they are reacting. This limits effective puppeteering,
especially when Bender is interacting with guests or family who are not aware Martin
is remote.

---

## Proposed Features

### 1. Ambient Mic Feed (near-term)

Keep the microphone open and stream ambient audio back to the puppet operator
(Martin) during puppet mode sessions.

**Requirements:**
- Low-latency audio stream from Bender mic → operator browser/UI
- Push-to-listen or always-on toggle (always-on preferred for presence feel)
- Must not interfere with wake-word detection or active STT sessions
- Volume normalisation so quiet room audio is audible
- Mute indicator visible in UI when streaming

**Implementation notes:**
- WebSocket audio stream from bender service → UI is the natural fit given existing WS infrastructure
- Mic is already open during wake-word listening phase; tap into that stream
- Need to handle mutex with STT (mute ambient feed while Bender is actively transcribing)

---

### 2. Live Video Feed (future — camera not yet fitted)

Framework to support a camera on Bender for live video back to the operator.

**Requirements:**
- MJPEG or WebRTC stream from Pi camera → operator UI
- Low enough latency to be useful for real-time puppeteering (~1–2s acceptable)
- UI panel alongside puppet controls (picture-in-picture or side panel)
- Camera hardware TBD (Pi Camera Module 3 or USB webcam)
- Should degrade gracefully when no camera is present (feature hidden, not broken)

**Implementation notes:**
- Pi Camera Module 3 (or HQ Camera) via libcamera is the natural choice for BenderPi
- `picamera2` + MJPEG over HTTP is simplest; WebRTC (via aiortc) if lower latency needed
- Video feed endpoint should be opt-in / gated behind puppet mode auth

---

## Acceptance Criteria

- [ ] Puppet mode UI shows ambient mic feed toggle
- [ ] Audio streams to operator within ~500ms of enabling
- [ ] No degradation to normal Bender wake-word / conversation pipeline
- [ ] Video panel appears in puppet UI when camera is detected (hidden otherwise)
- [ ] Both features work over the existing TLS/auth layer

---

## Dependencies

- Existing puppet mode WebSocket infrastructure
- Camera hardware (for video — not yet available)
- Possible: `picamera2`, `aiortc` packages

