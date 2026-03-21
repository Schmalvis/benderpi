# AI HAT+ 2 Integration Strategy — Design Spec

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Phased integration of the Raspberry Pi AI HAT+ 2 (Hailo-10H NPU) for local STT, local LLM inference, and local TTS.

---

## Problem Statement

BenderPi currently depends on:
- **CPU-bound Whisper** for STT — works but uses significant CPU and limits model size
- **Claude API** for all AI responses — adds latency (~1–2s round trip), costs money per query, requires internet
- **CPU-bound Piper** for TTS — fast enough but limits voice quality options

The AI HAT+ 2 (Hailo-10H, 40 TOPS, 8GB dedicated RAM) is already on the device but unused. It can accelerate inference for STT, LLMs, and potentially TTS, reducing cloud dependency and improving response latency.

---

## Hardware Constraints

| Constraint | Impact |
|---|---|
| **Single inference context** — Hailo-10H VDevice supports one active model at a time | Models must be serialized, not concurrent. Fits BenderPi's sequential listen→think→speak loop. |
| **8GB dedicated LPDDR4X** — NPU has its own memory | Models must fit in 8GB. Whisper-small (~460MB) + Qwen-1.5B (~3GB) fit comfortably. Not both simultaneously. |
| **40 TOPS INT4** — optimised for quantised models | Use INT4/INT8 quantised model variants for best performance. |
| **PCIe interface** — shares PCIe bus with Pi 5 | High-bandwidth data transfer between CPU and NPU. No bottleneck for audio-sized tensors. |
| **Context switching** — loading a new model takes time | Use the custom device manager pattern (hot-load services) to minimise switching latency. Pre-load models at startup. |

### Alignment with Existing Architecture

BenderPi's conversation loop is inherently sequential:
```
listen (STT) → classify (CPU) → respond (LLM/handler) → speak (TTS)
```

The NPU is never needed for two tasks simultaneously. Each phase simply replaces one stage's backend:

| Phase | STT | Intent | Response | TTS |
|---|---|---|---|---|
| Current | CPU (Whisper) | CPU (regex) | Cloud (Claude) | CPU (Piper) |
| Phase 1 | **NPU (Whisper)** | CPU (regex) | Cloud (Claude) | CPU (Piper) |
| Phase 2 | NPU (Whisper) | CPU (regex) | **NPU (Local LLM) / Cloud** | CPU (Piper) |
| Phase 3 | NPU (Whisper) | CPU (regex) | NPU (Local LLM) / Cloud | **NPU or CPU (TBD)** |

---

## Phase 1: NPU-Accelerated STT

### Goal
Move Whisper inference from CPU to Hailo-10H NPU. Faster transcription, lower CPU load, enables larger Whisper models for better accuracy.

### Design

**New module: `scripts/stt_hailo.py`**

Same public interface as `stt.py`:
```python
def listen_and_transcribe() -> str:
    """Record audio and transcribe using Hailo-accelerated Whisper."""
    ...
```

Internally:
- Uses Hailo's Python SDK to load a Whisper model onto the NPU
- Records audio the same way (PyAudio + VAD) — recording is CPU/audio-card bound, not NPU
- Sends recorded audio buffer to NPU for inference
- Returns transcribed text

**STT backend selection via config:**

```python
# config.py
self.stt_backend: str = "cpu"  # "cpu" | "hailo"
```

**Orchestrator change in `wake_converse.py`:**
```python
if cfg.stt_backend == "hailo":
    from stt_hailo import listen_and_transcribe
else:
    from stt import listen_and_transcribe
```

The rest of the pipeline is unchanged — `listen_and_transcribe()` returns a string regardless of backend.

**Model selection:**
```python
# config.py
self.stt_model: str = "whisper-small"  # replaces whisper_model
```

With NPU acceleration, `whisper-small` (460MB) becomes viable where CPU was limited to `whisper-base` or `whisper-tiny`. Better accuracy, especially for Bender-related queries where context matters.

**NPU lifecycle:**
- Model loaded once at startup (warm-up)
- Stays loaded across conversation sessions
- Released on service shutdown
- If NPU unavailable, fall back to CPU with a log warning

### Files Changed

| File | Changes |
|---|---|
| `scripts/stt_hailo.py` | New — Hailo-accelerated STT with same interface as `stt.py` |
| `scripts/config.py` | Add `stt_backend` attribute, rename `whisper_model` → `stt_model` |
| `bender_config.json` | Add `stt_backend: "cpu"`, `stt_model: "whisper-small"` |
| `scripts/wake_converse.py` | Conditional STT import based on `cfg.stt_backend` |
| `scripts/stt.py` | Use `cfg.stt_model` instead of hardcoded `WHISPER_MODEL` (from architecture refactor) |

### Validation
- Compare transcription accuracy: CPU Whisper-base vs NPU Whisper-small on 20 test utterances
- Measure latency: end-to-end STT time for a 5-second utterance
- Verify CPU load drops during transcription
- Verify fallback to CPU works when NPU is unavailable

---

## Phase 2: Local LLM for AI Responses

### Goal
Run a small local LLM on the Hailo-10H for responses that don't need Claude's full capability. Reduce API costs, latency, and cloud dependency. Claude API becomes a fallback for complex queries.

### Design

**New module: `scripts/ai_local.py`**

```python
class LocalAIResponder:
    """Local LLM responder using Hailo-10H NPU."""

    def __init__(self):
        self.history: list[dict] = []

    def respond(self, text: str, system_prompt: str | None = None) -> str:
        """Send text to local LLM, return response text."""
        ...

    def reset(self):
        """Clear conversation history."""
        self.history.clear()
```

Same interface shape as `AIResponder` in `ai_response.py` — both take text in, return text out.

**AI backend routing via config:**

```python
# config.py
self.ai_backend: str = "cloud"  # "cloud" | "local" | "hybrid"
```

- **`"cloud"`** — All AI responses go to Claude API (current behaviour)
- **`"local"`** — All AI responses go to local LLM (no API calls)
- **`"hybrid"`** (recommended default once Phase 2 is stable) — Smart routing based on query complexity

**Hybrid routing logic in `responder.py`:**

```python
def _respond_ai(self, text: str, ai_cloud, ai_local=None) -> Response:
    if cfg.ai_backend == "local" and ai_local:
        return self._generate_ai_response(text, ai_local)
    elif cfg.ai_backend == "hybrid" and ai_local:
        if self._is_simple_query(text):
            return self._generate_ai_response(text, ai_local)
        else:
            return self._generate_ai_response(text, ai_cloud)
    else:
        return self._generate_ai_response(text, ai_cloud)
```

**Simple vs complex classification:**

Simple queries (route to local LLM):
- Short questions (< 15 words)
- Conversational/social ("tell me a joke", "what do you think about X")
- Factual with context already available (time, date — but these are handled by ContextualHandler first)
- Follow-ups in an ongoing conversation where context is small

Complex queries (route to Claude):
- Long or multi-part questions
- Queries requiring reasoning, analysis, or code
- Anything the local model fails on (fallback)

```python
def _is_simple_query(self, text: str) -> bool:
    """Heuristic: short, conversational queries go to local LLM."""
    words = text.split()
    if len(words) > 20:
        return False
    # Complex signal words
    complex_signals = ["explain", "analyze", "compare", "write me",
                       "step by step", "how does", "why does"]
    text_lower = text.lower()
    return not any(signal in text_lower for signal in complex_signals)
```

**NPU context switching:**

Between STT and LLM, the NPU must switch models. This is managed by a device manager:

```python
# scripts/npu_manager.py
class NPUManager:
    """Manages Hailo-10H model loading and context switching."""

    def load_stt(self) -> None:
        """Load Whisper model onto NPU."""
        ...

    def load_llm(self) -> None:
        """Load LLM onto NPU."""
        ...

    def release(self) -> None:
        """Release NPU resources."""
        ...
```

The conversation loop becomes:
```
npu.load_stt()  →  listen_and_transcribe()
npu.load_llm()  →  local_ai.respond(text)
                →  tts_generate.speak(response)  # CPU, no NPU needed
npu.load_stt()  →  listen again...
```

Pre-loading strategy: Both models are loaded at startup into the device manager's model cache. Context switching swaps which one is active on the VDevice. The 8GB NPU RAM can hold both models in memory (Whisper-small ~460MB + Qwen-1.5B ~3GB = ~3.5GB of 8GB).

**Local LLM model selection:**

```python
# config.py
self.local_llm_model: str = "qwen-1.5b"  # Model identifier for Hailo
```

Candidate models (fit in 8GB with Whisper):
- Qwen 2.5 1.5B (INT4) — good multilingual, strong reasoning for size
- DeepSeek-R1-Distill 1.5B (INT4) — good at structured responses
- Llama 3.2 1B (INT4) — smallest, fastest, less capable

**System prompt for local LLM:**

The existing Bender system prompt from `ai_response.py` is reused, possibly trimmed for the smaller context window:

```
You are Bender Bending Rodriguez from Futurama. Stay in character.
Keep responses to 1-2 sentences. Be sarcastic, rude, and funny.
Never break character. Reference drinking, bending, and being great.
```

### Files Changed

| File | Changes |
|---|---|
| `scripts/ai_local.py` | New — Local LLM responder using Hailo SDK |
| `scripts/npu_manager.py` | New — NPU model lifecycle management |
| `scripts/config.py` | Add `ai_backend`, `local_llm_model` attributes |
| `bender_config.json` | Add `ai_backend: "cloud"`, `local_llm_model: "qwen-1.5b"` |
| `scripts/responder.py` | Accept optional `ai_local` parameter, add hybrid routing logic |
| `scripts/wake_converse.py` | Initialise `NPUManager` and `LocalAIResponder`, pass to responder |
| `scripts/stt_hailo.py` | Use `NPUManager` instead of direct Hailo SDK calls |

### Validation
- Compare response quality: local LLM vs Claude on 30 test prompts rated for in-character, accuracy, and coherence
- Measure latency: NPU context switch time (STT→LLM), local LLM inference time
- Test hybrid routing: verify simple queries go to local, complex to cloud
- Test fallback: when local model returns empty/garbage, verify cloud fallback triggers
- Verify API call reduction: count Claude API calls over a day with hybrid vs cloud-only

---

## Phase 3: Local TTS (Exploratory)

### Goal
Explore NPU-accelerated TTS as an alternative or complement to CPU-based Piper. This phase is the most speculative — it depends on model availability and quality.

### Current State of Local TTS on Hailo

As of March 2026, there is no well-documented NPU-accelerated TTS pipeline for Hailo-10H equivalent to Piper. The main candidates are:

- **XTTS / Coqui TTS models** — High quality, but large and not yet optimised for Hailo
- **VITS-based models** — Smaller, potentially convertible to Hailo format (HEF)
- **Custom Piper model on NPU** — Piper uses VITS internally; the ONNX model could theoretically be compiled to HEF format via Hailo's Model Zoo / Dataflow Compiler

### Design (Provisional)

**Same pattern as Phases 1 and 2:**

```python
# config.py
self.tts_backend: str = "cpu"  # "cpu" | "hailo"
```

**New module: `scripts/tts_hailo.py`**

Same interface as `tts_generate.py`:
```python
def speak(text: str) -> str:
    """Generate speech WAV using NPU-accelerated TTS. Returns temp WAV path."""
    ...

def warm_up() -> None:
    """Pre-load TTS model onto NPU."""
    ...
```

**NPU resource sharing with STT and LLM:**

Phase 3 adds a third model to the NPU rotation:
```
npu.load_stt()  →  listen
npu.load_llm()  →  generate response text
npu.load_tts()  →  generate speech audio
npu.load_stt()  →  listen again...
```

Three context switches per turn. Whether this is acceptable depends on switch latency (needs benchmarking). If too slow, TTS stays on CPU (Piper is already fast — ~200ms for a short sentence).

**Voice quality consideration:**

BenderPi uses a fine-tuned Piper model trained on Bender audio clips. Any NPU TTS must either:
1. Support the same fine-tuned model (if it's VITS/ONNX-based and convertible to HEF), or
2. Provide a voice cloning capability where Bender's voice can be reproduced

If neither is achievable, Phase 3 simply doesn't happen — Piper on CPU remains the TTS backend. The architecture supports both paths.

### Investigation Steps (Before Implementation)

1. Attempt to compile `bender.onnx` (Piper VITS model) to HEF format using Hailo Dataflow Compiler
2. Benchmark: HEF TTS inference time vs CPU Piper time — if NPU is not significantly faster, Phase 3 has no value
3. Compare audio quality: HEF output vs Piper output for 10 test sentences
4. Measure NPU context switch overhead for 3-model rotation (STT→LLM→TTS)

### Files Changed (Provisional)

| File | Changes |
|---|---|
| `scripts/tts_hailo.py` | New — NPU-accelerated TTS with same interface as `tts_generate.py` |
| `scripts/npu_manager.py` | Add `load_tts()` method, 3-model rotation support |
| `scripts/config.py` | Add `tts_backend` attribute |
| `bender_config.json` | Add `tts_backend: "cpu"` |
| `scripts/wake_converse.py` | Conditional TTS import based on `cfg.tts_backend` |

---

## NPU Manager Design (Cross-Phase)

The `NPUManager` is the central piece that enables all three phases to coexist. It must handle:

### Model Lifecycle

```python
class NPUManager:
    """Centralised Hailo-10H NPU resource manager."""

    def __init__(self):
        self._device = None      # Hailo VDevice
        self._models = {}        # name → loaded model handle
        self._active = None      # currently active model name

    def init(self, models: list[str]) -> None:
        """
        Initialise NPU and pre-load models into cache.
        models: list of model names to warm up, e.g. ["stt", "llm"]
        """
        ...

    def activate(self, model_name: str) -> object:
        """
        Switch active model on NPU. Returns model handle for inference.
        If model is already active, returns immediately (no-op).
        """
        ...

    def shutdown(self) -> None:
        """Release all NPU resources."""
        ...
```

### Key Behaviours

- **Lazy activation** — `activate("stt")` is a no-op if STT is already the active model
- **Graceful degradation** — If NPU init fails, `activate()` returns `None` and callers fall back to CPU
- **Thread safety** — Single lock guards all state transitions (matches BenderPi's single-threaded loop, but safe for web API calls)
- **Startup warm-up** — All configured models pre-loaded in `main()` before first wake word
- **Metrics integration** — Context switch times logged via `metrics.timer("npu_switch")`

### Configuration Summary (All Phases)

```json
{
    "stt_backend": "cpu",
    "stt_model": "whisper-small",
    "ai_backend": "cloud",
    "local_llm_model": "qwen-1.5b",
    "tts_backend": "cpu"
}
```

Phase 1 deployment: set `stt_backend: "hailo"`
Phase 2 deployment: set `ai_backend: "hybrid"`
Phase 3 deployment: set `tts_backend: "hailo"` (if viable)

Each phase is independently toggleable via config. Rolling back any phase is a single config change.

---

## Testing Strategy (All Phases)

### Phase 1
- Transcription accuracy: CPU vs NPU on 20 test utterances
- Latency benchmarks: STT inference time
- CPU load comparison: `htop` during conversation with CPU vs NPU STT
- Fallback: disable NPU, verify CPU backend activates with warning

### Phase 2
- Response quality: 30 test prompts rated by user for character, accuracy, humour
- Routing accuracy: verify `_is_simple_query()` classifications match expectations
- Latency: full turn (STT + context switch + LLM + TTS) vs current (STT + Claude API + TTS)
- API cost reduction: count Claude calls over 24h with hybrid vs cloud-only
- Fallback: set `ai_backend: "cloud"`, verify no local model loaded

### Phase 3
- Voice quality: A/B test NPU TTS vs Piper on 10 sentences (blind listening test)
- Latency: 3-model rotation timing per turn
- Model compatibility: verify Bender voice model converts to HEF without quality loss

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Hailo SDK API changes between versions | Pin SDK version in requirements.txt, test on update |
| Local LLM breaks character / gives unsafe responses | System prompt hardening + output validation + cloud fallback |
| NPU context switch too slow (>500ms) | Benchmark early in Phase 2. If too slow, keep STT on NPU and LLM on CPU |
| Bender voice model doesn't convert to HEF | Phase 3 is optional. Piper on CPU is the permanent fallback. |
| NPU thermal throttling under sustained use | Monitor CPU/NPU temps via watchdog. Add cooldown if needed. |
| 8GB NPU RAM insufficient for 3 models | Profile memory per model. Drop to 2 models if needed (STT stays on NPU, TTS stays on CPU). |

---

## Future Directions

- **Wake word on NPU** — Move Porcupine wake word detection from CPU to NPU (lower power idle state)
- **Local HA disambiguation** — Use local LLM instead of `difflib` for HA entity matching (from HA refactor spec)
- **Voice activity detection on NPU** — Replace webrtcvad with an NPU-accelerated VAD model for more accurate speech boundary detection
- **Multi-turn context on local LLM** — Manage conversation history within the local model's context window for more coherent multi-turn conversations
