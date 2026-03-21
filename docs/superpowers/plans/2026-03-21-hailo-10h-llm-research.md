# Hailo-10H LLM Research — 2026-03-21

Research findings on LLM text generation capabilities of the Hailo-10H NPU (AI HAT+ 2 for Raspberry Pi 5).

---

## 1. Hailo SDK GenAI Module

**Yes, HailoRT GenAI fully supports LLM text generation** — not just Speech2Text.

- **HailoRT GenAI** provides a complete LLM pipeline: tokenization, prefill, token-by-token decoding, KV caching — all offloaded to the NPU with zero host CPU utilisation during inference.
- **API access**: C++ API (direct HailoRT), Python API, and a REST API compatible with both Ollama and OpenAI formats.
- **Python GenAI apps**: Official examples at [hailo-ai/hailo-apps](https://github.com/hailo-ai/hailo-apps/blob/main/hailo_apps/python/gen_ai_apps/README.md) include `simple_whisper_chat` (STT + LLM + TTS pipeline).
- **Quantisation**: Uses QuaROT2 + GPTQ3 fused into the Hailo Dataflow Compiler. Models are A8W4 (8-bit activations, 4-bit weights).

**Source**: [Hailo Blog — LLM on Hailo-10H](https://hailo.ai/blog/bringing-generative-ai-to-the-edge-llm-on-hailo-10h/), [Hailo Software Suite](https://hailo.ai/products/hailo-software/hailo-ai-software-suite/)

---

## 2. Supported LLM Models (Hailo Model Zoo GenAI)

Pre-compiled HEF models from [hailo-ai/hailo_model_zoo_genai](https://github.com/hailo-ai/hailo_model_zoo_genai):

| Model | Parameters | Size | Context | TTFT | TPS | API |
|---|---|---|---|---|---|---|
| Qwen2-1.5B-Instruct | 1.5B | 1.56 GB | 2048 | 0.32s | **8.08** | C++, Python, Hailo-Ollama |
| Qwen2.5-Coder-1.5B-Instruct | 1.5B | 1.64 GB | 2048 | 0.32s | **8.07** | C++, Python, Hailo-Ollama |
| DeepSeek-R1-Distill-Qwen-1.5B | 1.5B | 2.37 GB | 2048 | 0.74s | **6.98** | C++, Python, Hailo-Ollama |
| Qwen2-1.5B-Instruct-Function-Calling-v1 | 1.5B | 2.99 GB | 2048 | 0.40s | **6.23** | C++, Python |
| Qwen2-VL-2B-Instruct (VLM) | 2B | 2.18 GB | 2048 | 0.97s | **6.73** | C++, Python |
| Whisper-Base (STT) | — | — | — | — | 23.36 | C++, Python |
| Whisper-Small (STT) | — | — | — | — | 8.71 | C++, Python |

**Not yet available**: Phi, Gemma, Mistral, Llama2-7B. Only 1B-1.5B LLMs currently. Llama 3.2 1B is referenced in some docs but not yet in the compiled model list.

**Notable**: Qwen2-1.5B-Instruct-Function-Calling-v1 is fine-tuned for tool/function calling — directly relevant to BenderPi's HA control use case.

---

## 3. Hailo-Ollama

**Yes, hailo-ollama exists and works.** It is an official Hailo project.

- Ollama-compatible REST API server written in C++ on top of HailoRT.
- Runs on `http://localhost:8000` by default.
- Supports standard Ollama endpoints: `/api/pull`, `/api/chat`, `/api/generate`.
- Compatible with **Open WebUI** frontend for browser-based chat.
- Models pulled via: `curl http://localhost:8000/api/pull -d '{"model": "qwen2:1.5b"}'`
- Install docs: [Raspberry Pi AI Documentation — LLMs section](https://www.raspberrypi.com/documentation/computers/ai.html#LLMs)

**Source**: [hailo_model_zoo_genai README](https://github.com/hailo-ai/hailo_model_zoo_genai), [Benchmarks blog](https://www.schwab.sh/blog/hailo-ai-hat-benchmarks/)

---

## 4. Community Projects

### Voice Chatbot (Whisper + LLM + Piper on Hailo-10H)
A community member built exactly what BenderPi would need: wake word → Whisper STT → Qwen2.5-1.5B LLM → Piper TTS, all on RPi5 + Hailo-10H. **Key gotcha**: you cannot run two models simultaneously on the Hailo-10H. Must unload Whisper before loading LLM, and vice versa. Model switching has reliability issues (release timeouts). Official fix: use `SHARED_VDEVICE_GROUP_ID` and properly release `SpeechtoTextProcessor` before `Vdevice`.

**Source**: [Hailo Community — Whisper Voice Chatbot](https://community.hailo.ai/t/whisper-voice-chatbot-on-rpi5-with-hailo-10h-works-but-has-issues-switching-models/18871)

### RPI-Hailo-Hat-Ollama
A project providing setup scripts and examples for running hailo-ollama on RPi5 + AI HAT+ 2.

**Source**: [DWestbury-PP/RPI-Hailo-Hat-Ollama](https://github.com/DWestbury-PP/RPI-Hailo-Hat-Ollama)

### Multi-Service AI Platform
Transforms the AI HAT+ 2 into a platform where up to 6 AI services can be loaded as hot APIs (though only one model can run at a time on the NPU).

**Source**: [gregm123456/raspberry_pi_hailo_ai_services](https://github.com/gregm123456/raspberry_pi_hailo_ai_services)

### be-more-hailo
Local AI agent running on Raspberry Pi with Hailo.

**Source**: [moorew/be-more-hailo](https://github.com/moorew/be-more-hailo/)

---

## 5. llama.cpp with Hailo

**No official llama.cpp backend for Hailo exists.** Status:

- A community developer created a fork with Hailo-10H support (`llama.cpp server and cli`), but it was **rejected from upstream** because the code was AI-generated.
- Feature requests exist: [Issue #11603](https://github.com/ggml-org/llama.cpp/issues/11603), [Issue #9181](https://github.com/ggml-org/llama.cpp/issues/9181).
- No vLLM or TensorRT-LLM support for Hailo either.
- The Hailo ecosystem uses its own HEF format and HailoRT runtime — fundamentally different from GGUF/llama.cpp.

**Source**: [Hailo Community — llama.cpp with Hailo 10H](https://community.hailo.ai/t/llama-cpp-server-and-cli-with-hailo-10h-support/18810)

---

## 6. Performance Benchmarks

### Hailo-10H (via hailo-ollama)

| Model | Tokens/sec | TTFT | Power |
|---|---|---|---|
| qwen2:1.5b | **8.03** | 0.32s | ~2.1W |
| qwen2.5-coder:1.5b | **7.94** | 0.32s | ~2.1W |
| deepseek-r1-distill-qwen:1.5b | **6.83** | 0.74s | ~2.1W |
| llama3.2:3b | **2.65** | — | ~2.1W |

### RPi5 CPU-only (via llama.cpp/Ollama, 8GB RAM)

| Model | Tokens/sec | Notes |
|---|---|---|
| gemma3:1b | ~10-18 | Most efficient small model |
| 1B models general | ~7-12 | Varies by model |
| 3B models (Q4) | ~4-7 | Usable for conversation |
| 7B models (Q4) | ~0.7-3 | Too slow for interactive use |

### Key Insight
**For 1.5B models, the Hailo-10H is roughly comparable to (or slightly slower than) the RPi5 CPU.** The CNX Software review confirmed this counterintuitive result. The Hailo's advantages are:
1. **CPU offload** — the Pi's CPU is free for other tasks (STT, wake word, TTS)
2. **Power efficiency** — 4.5 tokens/sec/watt, far better than CPU
3. **Dedicated 8GB RAM** — doesn't compete with system RAM

**Sources**: [Schwab Benchmarks](https://www.schwab.sh/blog/hailo-ai-hat-benchmarks/), [CNX Software Review](https://www.cnx-software.com/2026/01/20/raspberry-pi-ai-hat-2-review-a-40-tops-ai-accelerator-tested-with-computer-vision-llm-and-vlm-workloads/), [Stratosphere Lab](https://www.stratosphereips.org/blog/2025/6/5/how-well-do-llms-perform-on-a-raspberry-pi-5)

---

## 7. Alternative Approaches (Without Hailo)

### llama.cpp on RPi5 CPU
- Best option for pure CPU inference. ARM NEON optimised.
- gemma3:1b or Qwen2.5-1.5B at ~8-12 tok/s.
- Overclocking to 2.8GHz gives 10-15% boost.
- Needs active cooling for sustained inference.

### Ollama on RPi5
- Works out of the box, wraps llama.cpp.
- Same performance as llama.cpp but easier model management.
- `ollama run qwen2.5:1.5b` just works.

### Key models for RPi5 (any backend)
- **Qwen2.5-1.5B-Instruct** — best quality at 1.5B, good instruction following
- **gemma3:1b** — fastest, lowest resource usage
- **Llama 3.2 1B** — good general-purpose
- **DeepSeek-R1-Distill-Qwen-1.5B** — reasoning tasks

---

## 8. BenderPi-Specific Assessment

### If you buy the AI HAT+ 2 ($130)
**Pros**:
- Run LLM on NPU while CPU handles wake word (Porcupine), STT (faster-whisper), and TTS (Piper) — no resource contention
- Whisper STT could also run on Hailo (but model switching is painful — see gotcha below)
- Ollama-compatible API means easy integration
- Function-calling model (Qwen2-1.5B-FC) could replace regex intent classification for HA control

**Cons / Gotchas**:
- **Cannot run two models simultaneously** — only one model loaded on NPU at a time. This is a hard constraint.
- Model switching (e.g., Whisper → LLM) takes 4-8 seconds and has reliability issues
- Only 1.5B models are practical (3B drops to 2.65 tok/s)
- 1.5B models have limited reasoning ability compared to Claude Haiku
- 2048 token context length is short
- $130 hardware cost

### If you stay CPU-only
- Run Ollama/llama.cpp with Qwen2.5-1.5B at similar speeds
- BUT: CPU contention with wake word detection, STT, and TTS
- Practical only if you sequence operations (which BenderPi already does)

### Recommendation
The Hailo-10H is most valuable for BenderPi if you want to:
1. **Replace Claude API fallback** with a local LLM for simple queries (privacy, offline, no API cost)
2. **Keep CPU free** for concurrent wake word detection during LLM inference
3. Use the **function-calling model** to handle HA commands without regex

For BenderPi's current architecture (sequential: wake → STT → intent → respond → back to wake), CPU-only Ollama with Qwen2.5-1.5B is probably sufficient and $130 cheaper. The Hailo becomes compelling if you want simultaneous wake word + LLM or plan to add vision capabilities.
