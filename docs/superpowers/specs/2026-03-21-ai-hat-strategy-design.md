# AI HAT+ 2 Integration Strategy — Design Spec

**Date:** 2026-03-21 (Phase 1 reconciled: 2026-03-21)
**Status:** Phase 1 Implemented · Phases 2–3 Draft
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
| Phase 1 ✅ | **NPU (Whisper-Base)** | CPU (regex) | Cloud (Claude) | CPU (Piper) |
| Phase 2 | NPU (Whisper) | CPU (regex) | **NPU (Local LLM) / Cloud** | CPU (Piper) |
| Phase 3 | NPU (Whisper) | CPU (regex) | NPU (Local LLM) / Cloud | **NPU or CPU (TBD)** |

---

## Phase 1: NPU-Accelerated STT — IMPLEMENTED

### Goal
Move Whisper inference from CPU to Hailo-10H NPU. Faster transcription, lower CPU load, enables larger Whisper models for better accuracy.

### What Shipped (differs from original design)

The original design proposed a separate `stt_hailo.py` module with a config switch. The actual implementation took a simpler approach: **dual-backend auto-detection within `stt.py`**.

**Key design decision:** No config switch — the backend is selected automatically based on whether the Whisper HEF file exists at the known path. This eliminates configuration errors and means the same codebase runs on both NPU-equipped and CPU-only devices with zero config changes.

**Implementation in `scripts/stt.py`:**

```python
# Module-level state
_backend   = None   # "hailo" | "cpu"
_vdevice   = None   # Hailo VDevice (held open for lifetime of process)
_s2t       = None   # Hailo Speech2Text instance
_cpu_model = None   # faster-whisper fallback

WHISPER_HEF = "/usr/local/hailo/resources/models/hailo10h/Whisper-Base.hef"

def _load_model():
    """Initialise Hailo backend, falling back to faster-whisper on failure."""
    # 1. Check if HEF file exists → try Hailo
    # 2. If Hailo init fails → fall back to CPU with warning
    # 3. CPU uses cfg.whisper_model (default: "tiny.en")
```

**Transcription is backend-transparent:**
```python
def _transcribe_array(audio_array: np.ndarray) -> str:
    if _backend == "hailo":
        return _s2t.generate_all_text(audio_data=audio_array, ...)
    else:
        segments, _ = _cpu_model.transcribe(audio_array, ...)
        return " ".join(s.text for s in segments).strip()
```

**Public API unchanged** — `listen_and_transcribe()` works identically regardless of backend. No orchestrator changes needed.

**Additional features implemented:**
- Whisper hallucination filter (`WHISPER_HALLUCINATIONS` set) — filters known phantom outputs like "thank you", "subscribe"
- `_active_model_name()` returns `"whisper-base-hailo"` or `cfg.whisper_model` for metrics labelling
- `transcribe_file()` — transcribe pre-recorded WAV files (used by web UI)
- `VDevice` uses `group_id="SHARED"` for future multi-model sharing

### Files Changed (Actual)

| File | Changes |
|---|---|
| `scripts/stt.py` | Dual-backend init, Hailo `VDevice`/`Speech2Text` lifecycle, `_transcribe_array()` dispatch, hallucination filter, config via `cfg` singleton |
| `scripts/config.py` | `whisper_model`, `vad_aggressiveness`, `silence_frames`, `max_record_seconds` (from architecture refactor config unification) |

**Not created (diverged from original design):**
- `stt_hailo.py` — not needed; backends unified in `stt.py`
- `stt_backend` config key — not needed; auto-detected from HEF file
- `stt_model` config key — `whisper_model` kept for CPU fallback; Hailo model path is hardcoded

### Implications for Phase 2

The `VDevice` is currently held as a module-global in `stt.py` with `group_id="SHARED"`. For Phase 2 (local LLM), the NPU Manager concept needs to account for this:
- **Option A:** Extract `_vdevice` from `stt.py` into `npu_manager.py`, have both STT and LLM use the shared VDevice
- **Option B:** Keep STT's VDevice as-is, create a second VDevice for LLM (if Hailo supports multiple concurrent VDevices with `SHARED` group)
- The `SHARED` group ID suggests Option B may work, but needs benchmarking

### Validation Status
- Transcription accuracy: collecting data (HANDOVER priority)
- Hallucination rate monitoring: active via metrics
- CPU fallback: confirmed working (tested by removing HEF path)
- Latency benchmarks: pending (HANDOVER priority)

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

Between STT and LLM, the NPU must switch models. The current `stt.py` already holds a `VDevice` with `group_id="SHARED"`. The NPU Manager must either take ownership of this VDevice or coordinate with it.

**Recommended approach:** Extract the VDevice from `stt.py` into `npu_manager.py`. Both STT and LLM use the shared VDevice via the manager. This avoids dual-VDevice complexity and matches Hailo's single-inference-context constraint.

```python
# scripts/npu_manager.py
class NPUManager:
    """Manages Hailo-10H model loading and context switching."""

    def __init__(self):
        self._vdevice = None   # Shared VDevice (currently in stt.py)
        self._models = {}      # name → model handle
        self._active = None    # currently active model name

    def init(self, models: list[str]) -> None:
        """Initialise VDevice and pre-load models."""
        ...

    def activate(self, model_name: str) -> object:
        """Switch active model. No-op if already active."""
        ...

    def shutdown(self) -> None:
        """Release all NPU resources."""
        ...
```

The conversation loop becomes:
```
npu.activate("stt")  →  listen_and_transcribe()
npu.activate("llm")  →  local_ai.respond(text)
                     →  tts_generate.speak(response)  # CPU, no NPU needed
npu.activate("stt")  →  listen again...
```

**Migration note:** `stt.py` currently manages its own `_vdevice` and `_s2t` globals. Phase 2 must refactor these to use `NPUManager.activate("stt")` instead, returning the `Speech2Text` handle. The public `listen_and_transcribe()` API stays unchanged.

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
| `scripts/stt.py` | Refactor to use `NPUManager` for VDevice/Speech2Text instead of module globals |

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

**Migration from Phase 1:** The VDevice is currently managed as a module-global in `stt.py` (created during `_load_model()`). Phase 2 must extract this into `NPUManager`, which becomes the single owner of the Hailo VDevice. `stt.py` changes from managing its own VDevice to requesting the STT model handle from the manager.

### Model Lifecycle

```python
class NPUManager:
    """Centralised Hailo-10H NPU resource manager."""

    def __init__(self):
        self._device = None      # Hailo VDevice (extracted from stt.py)
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
    "whisper_model": "tiny.en",
    "ai_backend": "cloud",
    "local_llm_model": "qwen-1.5b",
    "tts_backend": "cpu"
}
```

Phase 1 deployment: automatic — HEF file presence enables Hailo STT (no config change needed). `whisper_model` is CPU fallback only.
Phase 2 deployment: set `ai_backend: "hybrid"`
Phase 3 deployment: set `tts_backend: "hailo"` (if viable)

Each phase is independently toggleable via config. Rolling back any phase is a single config change.

---

## Testing Strategy (All Phases)

### Phase 1 (implemented — validation pending)
- Transcription accuracy: CPU Whisper-tiny.en vs NPU Whisper-Base on 20 test utterances — **pending**
- Latency benchmarks: STT inference time — **pending** (tracked via `metrics.timer("stt_transcribe")`)
- CPU load comparison: `htop` during conversation with CPU vs NPU STT — **pending**
- Fallback: disable NPU, verify CPU backend activates with warning — **confirmed working**
- Hallucination rate: monitoring active via `metrics.count("stt_hallucination")`

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
