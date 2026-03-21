# Local LLM Integration — Design Spec

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Add local LLM inference via Ollama (CPU) with hybrid routing, quality-check escalation to Claude, scenario-based UI configuration, and rich logging for future classifier training.

---

## Problem Statement

BenderPi currently depends on the Claude API for all AI responses (UNKNOWN intent). This means:
- Every unhandled query costs money (API call)
- Responses require internet (no offline capability)
- Latency includes a network round trip (~1-2s)
- Privacy: all conversations go through a third-party API

The Raspberry Pi AI HAT+ 2 (Hailo-10H) is installed but only used for STT. Research shows that running LLMs on the Hailo-10H yields ~8 tok/s for 1.5B models — comparable to CPU performance on the Pi 5. Model switching between STT and LLM on the NPU takes 4-8 seconds with reliability issues, making per-turn switching impractical.

**Decision:** STT stays on the Hailo NPU (already working). LLM runs on the Pi 5 CPU via Ollama. This avoids model-switching overhead while keeping the CPU free during STT. The sequential conversation loop (listen → think → speak) means the CPU is idle during the "think" phase anyway.

**Note:** This decision should be revisited as Hailo SDK improves. If model switching becomes fast and reliable, moving LLM to the NPU would free CPU for concurrent tasks (e.g., vision).

---

## Constraints

- STT stays on Hailo NPU — no changes to `stt.py`
- Existing handlers (weather, HA, timers, contextual) are unaffected — they resolve before the AI path
- Claude API remains available as a fallback — quality of conversation should not regress
- Ollama is an external service dependency — BenderPi must handle it being unavailable
- WM8960 hardware constraint (mic/speaker mutual exclusion) unchanged
- BENDER_SYSTEM_PROMPT stays in one place — shared between local and cloud responders
- Existing conversation logging format extended, not replaced

---

## Design

### 1. Architecture Overview

```
User says something → intent.classify()
  → Handler match? → Handler responds (no LLM involved)
  → UNKNOWN intent? →
      classify_scenario(text) → conversation / knowledge / creative
      look up routing rule for scenario → local_first / local_only / cloud_only

      local_first:
        Local LLM generates response
          → quality check passes? → TTS → play
          → quality check fails?  → Claude API → TTS → play

      local_only:
        Local LLM generates response → TTS → play (even if poor quality)

      cloud_only:
        Claude API generates response → TTS → play (current behaviour)
```

**Components:**

| Component | Responsibility |
|---|---|
| `scripts/ai_local.py` | `LocalAIResponder` — talks to Ollama via HTTP, manages conversation history, quality check |
| `scripts/ai_response.py` | `AIResponder` — unchanged, remains the Claude backend |
| `scripts/responder.py` | `_respond_ai()` modified: scenario classification, routing, local-first with escalation |
| `scripts/config.py` | New config attributes for local LLM settings and routing rules |
| `bender_config.json` | Runtime configuration for AI backend, model, URL, timeout, routing |
| `scripts/conversation_log.py` | Extended turn logging with AI routing data |
| `scripts/web/app.py` | Config API extended for AI routing settings |
| `scripts/web/static/` | Config tab UI for AI backend controls |
| Ollama (systemd) | External service running Qwen2.5-1.5B on CPU |

### 2. LocalAIResponder (`scripts/ai_local.py`)

```python
import requests
from config import cfg
from ai_response import BENDER_SYSTEM_PROMPT
from logger import get_logger
from metrics import metrics
import tts_generate

log = get_logger("ai_local")

HEDGE_PHRASES = {"i'm not sure", "i don't know", "as an ai",
                 "i cannot", "i can't help", "i'm just a",
                 "language model", "i apologize"}


class QualityCheckFailed(Exception):
    """Raised when local LLM response fails quality check."""
    def __init__(self, reason: str, response_text: str):
        self.reason = reason
        self.response_text = response_text


def check_response_quality(text: str) -> tuple[bool, str]:
    """Return (passed, reason). Reason is empty string if passed."""
    if len(text.strip()) < 10:
        return False, "too_short"
    text_lower = text.lower()
    for phrase in HEDGE_PHRASES:
        if phrase in text_lower:
            return False, "hedge_phrase"
    return True, ""


class LocalAIResponder:
    def __init__(self):
        self.history: list[dict] = []
        self.max_history = 6

    def _trim_history(self):
        if len(self.history) > self.max_history * 2:
            self.history = self.history[-(self.max_history * 2):]

    def generate(self, user_text: str) -> str:
        """
        Generate a response via Ollama. Returns reply text only (no TTS).
        Raises QualityCheckFailed if response is poor quality.
        Raises requests.exceptions.* on connection/timeout errors.
        """
        self.history.append({"role": "user", "content": user_text})
        self._trim_history()

        with metrics.timer("ai_local_call", model=cfg.local_llm_model):
            resp = requests.post(
                f"{cfg.local_llm_url}/api/chat",
                json={
                    "model": cfg.local_llm_model,
                    "messages": [
                        {"role": "system", "content": BENDER_SYSTEM_PROMPT},
                        *self.history,
                    ],
                    "stream": False,
                    "options": {"num_predict": 150},
                },
                timeout=cfg.local_llm_timeout,
            )
            resp.raise_for_status()

        reply = resp.json()["message"]["content"].strip()
        self.history.append({"role": "assistant", "content": reply})

        passed, reason = check_response_quality(reply)
        if not passed:
            raise QualityCheckFailed(reason, reply)

        metrics.count("ai_local_success")
        return reply

    def clear_history(self):
        self.history = []
```

**Key design decisions:**
- `generate()` returns text only, not a WAV path. TTS is called by the responder after the quality check passes, avoiding wasted TTS generation on responses that get escalated.
- `BENDER_SYSTEM_PROMPT` is imported from `ai_response.py` — single source of truth.
- `QualityCheckFailed` carries both the reason and the response text, so logging can capture what the local model actually said.
- Conversation history is maintained independently from the cloud responder's history. If a query escalates, the cloud responder uses its own history (which may be empty or contain prior cloud turns). This is acceptable because escalation is infrequent.
- `num_predict: 150` matches the existing Claude `max_tokens: 150`.

### 3. Scenario Classification

Lightweight keyword-based classifier in `responder.py`:

```python
KNOWLEDGE_SIGNALS = {"who ", "what year", "when did", "where is",
                     "how many", "how far", "capital of", "invented",
                     "how does", "what is the", "explain", "define"}

CREATIVE_SIGNALS = {"tell me a joke", "sing", "insult", "roast",
                    "impression", "story", "poem", "rap"}

def _classify_scenario(self, text: str) -> str:
    t = text.lower()
    if any(s in t for s in KNOWLEDGE_SIGNALS):
        return "knowledge"
    if any(s in t for s in CREATIVE_SIGNALS):
        return "creative"
    return "conversation"
```

Each scenario maps to a routing rule via `cfg.ai_routing[scenario]`. The rule determines the local/cloud strategy.

**Future direction:** This keyword classifier is intentionally simple. Rich logging captures every classification + outcome. Once ~500+ labelled queries accumulate, this classifier can be replaced with a trained embedding-based model via Hugging Face. The logging schema is designed to produce a clean training dataset: `(query_text, scenario, local_succeeded, escalation_reason)`.

### 4. Responder Integration

**Updated `get_response` signature** (currently `get_response(self, text, ai=None)`):

```python
def get_response(self, text: str, ai=None, ai_local=None) -> Response:
    """Classify text and return the best Response.

    Args:
        text: transcribed user speech
        ai: AIResponder instance (Claude cloud fallback)
        ai_local: LocalAIResponder instance (local LLM, optional)
    """
    import intent as intent_mod

    with metrics.timer("response_total"):
        intent_name, sub_key = intent_mod.classify(text)
        log.info("Intent: %s%s", intent_name, f" / {sub_key}" if sub_key else "")

        for handler in self._dispatch.get(intent_name, []):
            try:
                resp = handler.handle(text, intent_name, sub_key)
                if resp is not None:
                    return resp
            except Exception as exc:
                log.warning("Handler %s failed for %s: %s",
                            type(handler).__name__, intent_name, exc)

        # No handler matched — AI fallback (with local-first routing)
        return self._respond_ai(text, ai, intent_name, sub_key, ai_local)
```

**Modified `_respond_ai`** in `responder.py`:

```python
def _respond_ai(self, text: str, ai_cloud, intent_name: str,
                sub_key: str | None, ai_local=None) -> Response:
    """AI fallback with local-first routing.

    Matches existing call convention: (text, ai, intent_name, sub_key)
    with ai_local appended as optional kwarg.
    """
    # Determine effective routing — ai_backend overrides per-scenario rules
    if cfg.ai_backend == "cloud_only" or ai_local is None:
        effective_routing = "cloud_only"
    elif cfg.ai_backend == "local_only":
        effective_routing = "local_only"
    else:
        scenario = self._classify_scenario(text)
        effective_routing = cfg.ai_routing.get(scenario, "local_first")

    scenario = getattr(self, '_last_scenario', self._classify_scenario(text))
    routing_log = {"scenario": scenario, "routing_rule": effective_routing}

    # Cloud-only path
    if effective_routing == "cloud_only":
        return self._generate_cloud_response(
            text, intent_name, sub_key, ai_cloud, routing_log)

    # Local-first or local-only path
    local_response_text = None
    local_latency_ms = None
    start = time.monotonic()
    try:
        local_response_text = ai_local.generate(text)
        local_latency_ms = int((time.monotonic() - start) * 1000)

        wav = tts_generate.speak(local_response_text)
        routing_log.update({
            "local_attempted": True,
            "local_response": local_response_text,
            "local_latency_ms": local_latency_ms,
            "quality_check_passed": True,
            "escalated_to_cloud": False,
            "final_method": "ai_local",
        })
        return Response(
            text=local_response_text, wav_path=wav,
            method="ai_local", intent=intent_name, sub_key=sub_key,
            is_temp=True, needs_thinking=True, routing_log=routing_log)

    except QualityCheckFailed as qcf:
        local_latency_ms = int((time.monotonic() - start) * 1000)
        local_response_text = qcf.response_text
        log.info("Local LLM quality check failed (%s), escalating", qcf.reason)
        routing_log.update({
            "local_attempted": True,
            "local_response": qcf.response_text,
            "local_latency_ms": local_latency_ms,
            "quality_check_passed": False,
            "quality_failure_reason": qcf.reason,
        })

    except Exception as e:
        local_latency_ms = int((time.monotonic() - start) * 1000)
        log.warning("Local LLM error: %s, escalating", e)
        routing_log.update({
            "local_attempted": True,
            "local_response": None,
            "local_latency_ms": local_latency_ms,
            "quality_check_passed": False,
            "quality_failure_reason": f"error:{type(e).__name__}",
        })

    # Escalate to cloud (unless local_only)
    if effective_routing == "local_only" and local_response_text:
        # Intentional: generate TTS for the rejected response —
        # local_only means use it regardless of quality check outcome.
        wav = tts_generate.speak(local_response_text)
        routing_log.update({
            "escalated_to_cloud": False,
            "final_method": "ai_local_forced",
        })
        return Response(
            text=local_response_text, wav_path=wav,
            method="ai_local_forced", intent=intent_name, sub_key=sub_key,
            is_temp=True, needs_thinking=True, routing_log=routing_log)

    return self._generate_cloud_response(
        text, intent_name, sub_key, ai_cloud, routing_log)
```

`_generate_cloud_response` wraps the existing `AIResponder.respond()` call and adds cloud-side routing log fields before returning.

**Note:** `routing_log` is passed through to `conversation_log.py` as part of the turn entry. It's a dict that accumulates fields as the routing decision progresses.

### 5. Conversation Logging

**Updated `log_turn` signature** (currently has fixed params, no extras):

```python
def log_turn(self, user_text: str, intent: str, sub_key: str | None,
             method: str, response_text: str = "", model: str | None = None,
             ai_routing: dict | None = None):
    self.turn += 1
    entry = {
        "type":          "turn",
        "session_id":    self.session_id,
        "turn":          self.turn,
        "user_text":     user_text,
        "intent":        intent,
        "sub_key":       sub_key,
        "method":        method,
        "response_text": response_text,
        "model":         model,
    }
    if ai_routing:
        entry["ai_routing"] = ai_routing
    _write(entry)
```

The caller in `wake_converse.py` passes `ai_routing=response.routing_log` when logging turns. For handler-resolved intents, `response.routing_log` is `None` and the field is omitted from the log entry.

Each turn in `logs/YYYY-MM-DD.jsonl` gains an optional `ai_routing` field:

**Example: local succeeded**
```json
{
    "type": "turn",
    "user_text": "What do you think of humans?",
    "intent": "UNKNOWN",
    "method": "ai_local",
    "ai_routing": {
        "scenario": "conversation",
        "routing_rule": "local_first",
        "local_attempted": true,
        "local_response": "Humans? Pfft. You're all just walking bags of water...",
        "local_latency_ms": 3800,
        "quality_check_passed": true,
        "escalated_to_cloud": false,
        "final_method": "ai_local"
    }
}
```

**Example: local failed, escalated to cloud**
```json
{
    "type": "turn",
    "user_text": "Who was the first king of Spain?",
    "intent": "UNKNOWN",
    "method": "ai_fallback",
    "ai_routing": {
        "scenario": "knowledge",
        "routing_rule": "local_first",
        "local_attempted": true,
        "local_response": "I'm not sure about that, but...",
        "local_latency_ms": 4200,
        "quality_check_passed": false,
        "quality_failure_reason": "hedge_phrase",
        "escalated_to_cloud": true,
        "cloud_response": "That'd be Charles I, though humans call him Carlos...",
        "cloud_latency_ms": 1100,
        "final_method": "ai_fallback"
    }
}
```

When no AI routing occurs (handler-resolved intents), the `ai_routing` field is absent.

**Data retention:** Logs stay on the Pi's SD card (gitignored). For classifier training, a future script extracts `(query_text, scenario, local_succeeded, escalation_reason)` across all JSONL files.

### 6. Configuration

**`config.py` additions:**

```python
self.ai_backend: str = "hybrid"
self.local_llm_model: str = "qwen2.5:1.5b"
self.local_llm_url: str = "http://localhost:11434"
self.local_llm_timeout: int = 6
self.ai_routing: dict = {
    "conversation": "local_first",
    "knowledge": "local_first",
    "creative": "local_first",
}
```

**`bender_config.json` additions:**

```json
{
    "ai_backend": "hybrid",
    "local_llm_model": "qwen2.5:1.5b",
    "local_llm_url": "http://localhost:11434",
    "local_llm_timeout": 6,
    "ai_routing": {
        "conversation": "local_first",
        "knowledge": "local_first",
        "creative": "local_first"
    }
}
```

**`ai_backend` values:**
- `"hybrid"` — scenario-based routing (default)
- `"local_only"` — all AI responses go local, no escalation
- `"cloud_only"` — all AI responses go to Claude (current behaviour, useful as kill switch)

**`ai_routing` per-scenario values:**
- `"local_first"` — try local, escalate to cloud on quality failure
- `"local_only"` — local only, use response even if poor quality
- `"cloud_only"` — skip local, go straight to Claude

### 7. Web UI Config Tab

New "AI Backend" section on the Config tab:

| Control | Type | Maps to |
|---|---|---|
| AI Backend | Dropdown: hybrid / local_only / cloud_only | `ai_backend` |
| Local LLM Model | Text input | `local_llm_model` |
| Local LLM URL | Text input | `local_llm_url` |
| Local LLM Timeout | Slider (1-15s) | `local_llm_timeout` |
| Conversation routing | Dropdown: local_first / local_only / cloud_only | `ai_routing.conversation` |
| Knowledge routing | Dropdown: local_first / local_only / cloud_only | `ai_routing.knowledge` |
| Creative routing | Dropdown: local_first / local_only / cloud_only | `ai_routing.creative` |

Uses the existing config API (`/api/config` GET/POST) — the `ai_routing` nested dict is serialised as part of the JSON config.

### 8. Orchestrator Changes (`wake_converse.py`)

Minimal changes:

```python
from ai_local import LocalAIResponder

# In main():
ai_local = None
if cfg.ai_backend != "cloud_only":
    try:
        ai_local = LocalAIResponder()
        log.info("Local AI responder initialised (model: %s)", cfg.local_llm_model)
    except Exception as e:
        log.warning("Local AI init failed: %s — cloud-only mode", e)

# In run_session():
response = responder.get_response(text, ai=ai_cloud, ai_local=ai_local)
```

`get_response` signature gains an optional `ai_local` parameter. If `None`, the responder behaves exactly as before (cloud-only).

### 9. Ollama Service Setup

Ollama is installed and managed separately from BenderPi:

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull the model
ollama pull qwen2.5:1.5b

# Ollama auto-installs as: ollama.service
sudo systemctl enable ollama
sudo systemctl start ollama
```

**Not managed by BenderPi code** — Ollama is infrastructure, like Home Assistant. BenderPi connects to it via HTTP. If it's not running, graceful degradation to cloud-only.

---

## Files Changed

### New files

| File | Purpose |
|---|---|
| `scripts/ai_local.py` | `LocalAIResponder`, `QualityCheckFailed`, `check_response_quality()` |

### Modified files

| File | Changes |
|---|---|
| `scripts/config.py` | Add `ai_backend`, `local_llm_model`, `local_llm_url`, `local_llm_timeout`, `ai_routing` |
| `bender_config.json` | Add local LLM config keys with defaults |
| `scripts/responder.py` | Add `_classify_scenario()`, modify `_respond_ai()` for local-first routing, accept `ai_local` param |
| `scripts/wake_converse.py` | Initialise `LocalAIResponder`, pass to `responder.get_response()` |
| `scripts/conversation_log.py` | Accept and write `ai_routing` dict in turn entries |
| `scripts/web/app.py` | Config API handles `ai_routing` nested dict |
| `scripts/web/static/app.js` | Config tab: AI backend controls |
| `scripts/handler_base.py` | Add `routing_log: dict \| None = None` field to `Response` dataclass |
| `scripts/web/static/style.css` | Styling for new config controls (if needed) |
| `HANDOVER.md` | Ollama install instructions, log retention note |

### Unchanged files

| File | Why |
|---|---|
| `scripts/ai_response.py` | `AIResponder` and `BENDER_SYSTEM_PROMPT` unchanged (prompt imported by `ai_local.py`) |
| `scripts/stt.py` | STT stays on Hailo, no changes |
| `scripts/audio.py` | No changes |
| `scripts/intent.py` | No changes |
| All handler files | Handlers resolve before AI path, unaffected |

---

## Testing Strategy

- **LocalAIResponder:** Unit test with mocked HTTP responses — test successful generation, quality check failures (too_short, hedge_phrase), timeout, connection error
- **Quality check:** Unit test `check_response_quality()` — edge cases for length threshold, each hedge phrase, clean responses
- **Scenario classifier:** Unit test `_classify_scenario()` — knowledge/creative/conversation examples
- **Responder routing:** Unit test `_respond_ai()` with mocked local+cloud responders — test all routing paths (local_first success, local_first escalation, local_only forced, cloud_only, no local available)
- **Conversation logging:** Verify `ai_routing` field appears in JSONL when AI path is taken, absent when handler resolves
- **Config:** Verify `ai_routing` dict round-trips through config API
- **Graceful degradation:** Test with Ollama not running — verify cloud fallback with warning log

---

## Edge Cases

- **Ollama not running** — Connection error on first local call, escalates to cloud. Warning logged. No crash.
- **Ollama running but model not pulled** — 404 from Ollama API, treated as error, escalates to cloud.
- **Local response is empty string** — Fails quality check (too_short), escalates.
- **Local model returns Bender-breaking response** — Hedge phrase detection catches common AI-assistant-isms. Escalates to cloud.
- **Both local and cloud fail** — Existing `_error_response` path in responder handles this (plays a pre-built error clip).
- **Config changed via UI mid-session** — Config is read per-call (cfg singleton reloads from file). Changes take effect on next query.
- **Internet down, ai_backend=hybrid** — Local succeeds normally. If quality check fails, cloud escalation also fails. Falls through to error response. Consider: if `ai_backend=hybrid` and cloud is unreachable, treat it as `local_only` for that call? (Stretch goal, not in initial implementation.)
- **Conversation history divergence** — Local and cloud responders maintain independent histories. If a conversation starts local and escalates mid-way, the cloud responder lacks context from earlier local turns. Acceptable for Bender's short responses. Could be improved later by sharing history.

---

## Future Directions

- **Self-learning classifier** — Replace keyword-based `_classify_scenario()` with a trained embedding model once ~500+ labelled queries are logged. Training pipeline via Hugging Face. Tracked in project memory.
- **Hailo NPU for LLM** — Revisit if model switching improves in future Hailo SDK releases. The `local_llm_url` config makes this a seamless switch: point at `http://localhost:8000` (hailo-ollama) instead of `http://localhost:11434` (CPU Ollama).
- **Shared conversation history** — Merge local and cloud histories so escalated queries have full context.
- **Function-calling model** — Qwen2-1.5B-FC (available in Hailo model zoo) could replace regex intent classification for HA control.
- **Streaming responses** — Stream tokens from Ollama to start TTS earlier (first sentence ready before full response completes). Would reduce perceived latency.
- **Adaptive routing thresholds** — Track escalation rates per scenario. Auto-switch to `cloud_only` if a scenario escalates >80% of the time.
