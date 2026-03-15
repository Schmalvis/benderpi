# Raspberry Pi AI HAT+ Analysis for BenderPi

## Research Report: Could an NPU Accelerator Improve the BenderPi Voice Assistant?

**Date:** 2026-03-15
**Project:** BenderPi (Raspberry Pi 5 Voice Assistant)
**Author:** AI-assisted research (Claude)

> **Note:** This analysis is based on knowledge current to approximately mid-2025. The Hailo ecosystem evolves rapidly -- verify specific model availability and software versions against the latest Hailo and Raspberry Pi documentation before purchasing.

---

## 1. What IS the Raspberry Pi AI HAT+?

### Hardware Overview

The Raspberry Pi AI HAT+ is an official Raspberry Pi add-on board that attaches via the Pi 5's PCIe connector (the FFC/FPC connector on the board). It contains a **Hailo AI accelerator** chip dedicated to neural network inference.

There are **two variants**:

| Variant | NPU Chip | Performance | Approximate Price |
|---|---|---|---|
| **AI HAT+ (13 TOPS)** | Hailo-8L | 13 TOPS (INT8) | ~$26 |
| **AI HAT+ (26 TOPS)** | Hailo-8 | 26 TOPS (INT8) | ~$70 |

A subsequent product, the **AI HAT 2+**, was announced/released using a newer Hailo chip generation. Based on available information up to mid-2025, this uses the **Hailo-10H** with up to **40 TOPS** and includes a built-in vision ISP. However, the AI HAT 2+ is primarily targeted at camera/vision pipelines and its relevance to audio/NLP workloads may be limited -- the original AI HAT+ is more pertinent to BenderPi's needs.

### Key Specifications (AI HAT+ with Hailo-8L / Hailo-8)

| Spec | Hailo-8L (13 TOPS) | Hailo-8 (26 TOPS) |
|---|---|---|
| Peak throughput | 13 TOPS (INT8) | 26 TOPS (INT8) |
| Interface | PCIe Gen 3 x1 (via FFC) | PCIe Gen 3 x1 (via FFC) |
| Power draw | ~1.5W typical | ~2.5W typical |
| On-chip memory | ~2 MB SRAM | ~4 MB SRAM |
| Host memory | Uses system RAM for model weights | Uses system RAM for model weights |
| Data types | INT8, INT4 (quantized) | INT8, INT4 (quantized) |
| Form factor | HAT+ standard, sits on top of Pi 5 | HAT+ standard, sits on top of Pi 5 |

### Critical Architecture Detail

The Hailo chips are **fixed-function neural network accelerators**, not general-purpose GPUs. They excel at running pre-compiled, quantized neural network graphs. Models must be:

1. Exported from the training framework (PyTorch/TensorFlow/ONNX)
2. Quantized to INT8 (or INT4)
3. Compiled using the **Hailo Dataflow Compiler (DFC)** into an `.hef` (Hailo Executable Format) file
4. Run via the **HailoRT** runtime library

This compilation step is non-trivial and represents the largest adoption barrier.

---

## 2. Workload-by-Workload Analysis

### 2a. Wake Word Detection

**Current:** Porcupine (Picovoice) on CPU at 16kHz
**Could the AI HAT+ help?** Possibly, but low priority.

| Factor | Assessment |
|---|---|
| Current bottleneck? | **No** -- Porcupine is highly optimized, uses <5% CPU |
| NPU alternative | OpenWakeWord (uses small CNN/RNN models) could run on Hailo |
| Expected benefit | Marginal speed improvement; main benefit would be eliminating the Picovoice API key dependency |
| Model availability | OpenWakeWord models would need conversion to HEF format -- no pre-compiled Hailo models exist |
| Effort | Medium -- model conversion + custom wake word training |

**Verdict:** Low priority. Porcupine already works well. The only compelling reason to move wake word detection to the NPU would be to eliminate the proprietary Picovoice dependency. OpenWakeWord is a viable open-source alternative but runs fine on CPU too.

### 2b. Speech-to-Text (Whisper)

**Current:** faster-whisper with `tiny.en` on CPU (~1-2s inference, low accuracy)
**Could the AI HAT+ help?** This is where it gets complicated.

| Factor | Assessment |
|---|---|
| Current bottleneck? | **Partially** -- `tiny.en` is fast but inaccurate; larger models are too slow on CPU |
| Whisper on Hailo | Whisper is a **Transformer** (encoder-decoder with attention). Hailo-8/8L support for Transformer architectures is **limited**. |
| Encoder portion | The Whisper encoder (Conv + Transformer) could potentially be compiled, but attention layers may not map efficiently |
| Decoder portion | Autoregressive decoding (token-by-token) is **poorly suited** to the Hailo dataflow architecture |
| Pre-built models? | As of mid-2025, **no official Whisper HEF models** exist in the Hailo Model Zoo |
| Alternative: whisper.cpp | Could use whisper.cpp with CPU optimizations (ARM NEON) for better performance without NPU |

**The core problem:** Hailo's architecture is optimized for CNNs and feed-forward networks, not autoregressive Transformer decoders. The encoder portion of Whisper could theoretically be offloaded, but the decoder (which generates tokens one at a time) would still need to run on CPU. The speedup would be partial at best.

**Better alternatives for STT improvement (without AI HAT+):**
- Use `whisper-base.en` with faster-whisper's INT8 quantization on CPU (Pi 5 is reasonably capable)
- Try `distil-whisper` models (distil-small.en) which are faster with comparable accuracy
- Use streaming/chunked processing to reduce perceived latency

**Verdict:** Moderate potential but high effort and uncertain payoff. The Hailo architecture is not ideal for Transformer models. Improving STT via better CPU-side model selection (distil-whisper, base.en with quantization) is likely more practical.

### 2c. Text-to-Speech (Piper / VITS)

**Current:** Piper TTS via subprocess call, ~0.5-1s cold start, 22050Hz output
**Could the AI HAT+ help?** Potentially yes, and this is one of the more promising workloads.

| Factor | Assessment |
|---|---|
| Current bottleneck? | **Yes** -- subprocess cold start adds 0.5-1s latency per utterance |
| VITS architecture | VITS (used by Piper) is primarily **convolutional** with some attention -- better suited to Hailo than Transformer-heavy models |
| Model conversion | The ONNX model would need to be converted via Hailo DFC. VITS models have variable-length output (audio), which complicates compilation |
| Variable-length output | Hailo models prefer **fixed input/output shapes**. Audio generation with variable-length sequences is a challenge |
| Cold start fix | Even without the NPU, the cold start can be eliminated by running Piper as a **persistent process** (long-running server) rather than a subprocess per utterance |

**The simpler fix:** Before investing in NPU acceleration, the subprocess cold start should be eliminated by running Piper as a persistent Python process (using `piper-phonemize` + the ONNX runtime directly, or running Piper in `--server` mode). This alone would reduce TTS latency from ~0.5-1s to ~0.1-0.3s on CPU.

**Verdict:** The NPU could help, but the **low-hanging fruit is fixing the subprocess architecture first**. If TTS is still too slow after that, NPU acceleration of the VITS encoder/decoder convolutions could provide further speedup, but model conversion would require significant effort.

### 2d. Intent Classification

**Current:** Regex/keyword matching (fast but inflexible)
**Could the AI HAT+ help?** Yes -- this is a strong candidate.

| Factor | Assessment |
|---|---|
| Current bottleneck? | **Not speed, but capability** -- regex can't handle paraphrases or fuzzy intent |
| Suitable models | DistilBERT, TinyBERT, MobileBERT -- small classification models ideal for NPU |
| Hailo compatibility | BERT-style encoder models (no autoregressive decoding) **map well** to Hailo |
| Pre-built models? | Some BERT variants exist in the Hailo Model Zoo for NLP tasks |
| Expected inference time | Sub-10ms for a small classifier on the NPU |
| Effort | Medium -- fine-tune a small BERT on your intents, export to ONNX, compile to HEF |

**However:** A small DistilBERT intent classifier also runs perfectly fast on the Pi 5 CPU (10-50ms with ONNX Runtime). The NPU benefit is marginal for single-inference classification. The real question is whether you want ML-based intent classification at all -- and that decision is independent of the NPU.

**Verdict:** The NPU is capable but overkill for this task. A small ONNX model on CPU would work just as well. The valuable investment here is **training the classifier**, not accelerating it.

### 2e. Local LLM (Replacing Claude API)

**Current:** Claude API (Haiku) with 2-3s latency
**Could the AI HAT+ help?** No -- this is the clearest "no" in the analysis.

| Factor | Assessment |
|---|---|
| Current bottleneck? | **Yes** -- API round-trip adds 2-3s |
| LLM on Hailo? | **Not feasible.** LLMs are autoregressive Transformers -- the worst case for Hailo's dataflow architecture |
| Memory constraint | Even a 2B parameter model (TinyLlama, Gemma-2B) at INT4 needs ~1-1.5 GB. Hailo has only 2-4 MB on-chip SRAM. Weights stream from host RAM over PCIe, which **severely bottlenecks** token generation |
| Token generation | LLMs generate one token at a time. Each token requires a full model forward pass. Hailo's batch-oriented dataflow architecture adds overhead per invocation |
| Realistic performance | Even if you got it working, expect **<5 tokens/second** -- far worse than just running llama.cpp on the Pi 5 CPU |

**Better alternatives for reducing API dependency:**
- Run `llama.cpp` with a small quantized model (Phi-3-mini-Q4, TinyLlama-Q4) directly on the Pi 5's **CPU** -- expect 5-15 tokens/s with ARM NEON, which is passable for short responses
- Use a hybrid approach: regex intents + small local model for common queries, API fallback only for complex ones
- Cache frequent AI responses (you already have the promotion system)

**Verdict:** The AI HAT+ cannot meaningfully accelerate LLM inference. For local LLM, use the CPU with llama.cpp. The Hailo NPU is architecturally unsuited to autoregressive text generation.

---

## 3. Supported Frameworks and Runtime

### Hailo Software Stack

| Component | Description |
|---|---|
| **Hailo Dataflow Compiler (DFC)** | Offline tool that converts models to HEF format. Runs on x86 Linux (not on the Pi itself). Requires a calibration dataset for quantization. |
| **HailoRT** | C/C++ and Python runtime for loading and running HEF models on the device |
| **TAPPAS** | High-level application framework, primarily for vision pipelines (GStreamer-based) |
| **Hailo Model Zoo** | Pre-compiled HEF files for common models (mostly vision: YOLO, ResNet, MobileNet, etc.) |
| **rpicam-apps integration** | Raspberry Pi camera stack integration for real-time vision AI |

### Supported Input Formats for Compilation

- PyTorch (via ONNX export)
- TensorFlow / TF Lite
- ONNX
- Keras (via TF)

### Key Limitation: The Compilation Step

The Hailo DFC is **not** like running ONNX Runtime or TFLite where you just load a model file. The compilation process:

1. Parses the model graph
2. Maps operations to Hailo's dataflow cores
3. Quantizes to INT8 using a calibration dataset (typically 50-100 representative samples)
4. Optimizes memory allocation and data flow scheduling
5. Produces a fixed `.hef` binary

**Not all operations are supported.** Unsupported ops cause compilation failures. Common issues:
- Dynamic shapes (variable sequence lengths)
- Some attention mechanisms
- Custom/exotic activation functions
- Certain reshape/transpose patterns

This means you cannot simply "drop in" any ONNX model -- there is real engineering work per model.

---

## 4. Limitations and Gotchas

### 4.1 PCIe Lane Sharing

The Pi 5 has a **single PCIe Gen 2 x1 lane** exposed via the FFC connector (the AI HAT+ runs it in Gen 3 x1 mode via negotiation). This means:

- **The AI HAT+ occupies the only PCIe slot.** You cannot simultaneously use an NVMe SSD and the AI HAT+ without a PCIe switch/multiplexer (which adds complexity and may reduce bandwidth).
- If you were planning to add NVMe storage for faster model loading, this is a conflict.

### 4.2 Physical Compatibility with Voice Bonnet

This is a **critical concern for BenderPi:**

| Issue | Details |
|---|---|
| GPIO conflict | The AI HAT+ uses the PCIe FFC connector, NOT the GPIO header. However, it physically sits on top of the Pi and may interfere with the Voice Bonnet's I2S GPIO connections |
| Stacking | The Voice Bonnet (Adafruit) uses the 40-pin GPIO header. The AI HAT+ uses the separate PCIe FFC connector. In theory they can coexist, but **physical clearance** may be an issue -- you would need standoffs and careful stacking |
| Power budget | Pi 5 draws ~5W, Voice Bonnet adds ~0.5W, AI HAT+ adds ~1.5-2.5W. Total: ~7-8W. A good 5V/5A USB-C power supply (the official Pi 5 PSU) should handle this, but verify |
| I2C/SPI conflicts | The AI HAT+ has a small EEPROM on I2C for HAT identification. Your WS2812B LEDs use SPI (GPIO 10). No conflict expected, but test carefully |

### 4.3 Model Conversion Effort

| Model Type | Conversion Difficulty | Notes |
|---|---|---|
| CNNs (image classifiers) | Easy | Well-supported, many examples in Model Zoo |
| Object detection (YOLO) | Easy | Pre-built HEFs available |
| BERT-style encoders | Medium | Fixed sequence length required, some attention patterns need workarounds |
| VITS/TTS | Hard | Variable-length audio output, complex architecture |
| Whisper | Hard | Encoder possible, decoder problematic |
| LLMs | Not feasible | Architecture mismatch |

### 4.4 Software Maturity

- The Hailo ecosystem is **vision-first**. Most examples, documentation, and pre-built models are for camera/video pipelines.
- Audio/NLP workloads are second-class citizens in the ecosystem.
- Community support for non-vision use cases is thin.
- The DFC compiler runs on **x86 Linux only** -- you need a separate machine (or Docker container) to compile models.

### 4.5 Quantization Accuracy Loss

All models run in INT8 (or INT4) on the Hailo. For:
- **Vision models:** accuracy loss is typically <1%, well-studied
- **Audio models (Whisper):** quantization effects on WER (word error rate) are less well-characterized and could be meaningful
- **TTS models (VITS):** INT8 quantization can introduce audible artifacts in generated speech -- quality testing is essential

---

## 5. Recommended Adoption Strategy

### Priority Matrix

| Priority | Workload | Approach | NPU Needed? | Expected Impact |
|---|---|---|---|---|
| **1 (Do Now)** | TTS cold start | Run Piper as persistent process, not subprocess | **No** | Eliminate 0.5-1s latency per utterance |
| **2 (Do Now)** | STT accuracy | Switch to `distil-whisper-small.en` or `base.en` with faster-whisper INT8 on CPU | **No** | Significant accuracy improvement, minor speed cost |
| **3 (Do Soon)** | Intent classification | Train a small DistilBERT classifier on your intents, run via ONNX Runtime on CPU | **No** | Much better intent recognition, handles paraphrases |
| **4 (Consider)** | Local LLM fallback | Run Phi-3-mini or similar via llama.cpp on CPU for simple queries | **No** | Reduce API calls by 50-70%, 1-2s per response |
| **5 (Maybe)** | NPU for TTS | If TTS is still too slow after #1, investigate Hailo acceleration of VITS | **Yes** | Marginal further speedup |
| **6 (Low)** | NPU for wake word | Move to OpenWakeWord on NPU to drop Picovoice dependency | **Yes** | Eliminates API key requirement |

### The Honest Assessment

**For BenderPi's current workloads, the AI HAT+ is not recommended as an immediate purchase.** Here is why:

1. **The biggest wins are software changes, not hardware.** Fixing the TTS subprocess issue and upgrading the Whisper model are pure software improvements that cost nothing.

2. **The NPU's strengths (vision CNNs) don't align with BenderPi's needs (audio, NLP, text generation).** The Hailo chip was designed for real-time video analytics, not for the Transformer-heavy workloads that dominate a voice assistant.

3. **The model conversion effort is substantial.** Each model you want to run on the NPU requires hours-to-days of engineering work with the Hailo DFC, calibration data preparation, and accuracy validation. For a hobby project, this is a significant investment.

4. **The one workload where an NPU would truly shine (local LLM) is the one it cannot do.** The architectural mismatch between autoregressive text generation and Hailo's dataflow design is fundamental.

### When WOULD the AI HAT+ Make Sense for BenderPi?

- If you add a **camera** for visual interaction (face detection, gesture recognition, object identification) -- this is the AI HAT+'s sweet spot
- If the Hailo ecosystem matures to include **pre-compiled audio/NLP models** (Whisper, VITS, BERT) in the Model Zoo, reducing the conversion burden
- If a future Hailo chip generation adds better **Transformer/attention support**
- If you want to run **continuous background AI tasks** (e.g., ambient sound classification, presence detection) without loading the CPU

---

## 6. Alternative Acceleration Options

For completeness, here are other approaches that might better serve BenderPi's needs:

| Option | Best For | Notes |
|---|---|---|
| **CPU optimization** (ARM NEON, multi-threading) | All workloads | Free, no hardware needed. faster-whisper already uses CTranslate2 with ARM optimizations |
| **USB Coral TPU** (Google Edge TPU) | Small classifiers, wake word | 4 TOPS, good TFLite support, but limited model size (~8MB on-chip) |
| **ONNX Runtime with ARM optimizations** | Intent classification, TTS | Already available, no special hardware |
| **llama.cpp on CPU** | Local LLM | Best option for local text generation on Pi 5 |
| **Remote inference server** | Any heavy model | If you have a GPU machine on your LAN, offload heavy inference there with ~1ms network latency |

---

## 7. Summary

| Question | Answer |
|---|---|
| Is the AI HAT+ a good fit for BenderPi? | **Not today.** The workload-hardware alignment is poor for a voice assistant. |
| What should be done first? | Fix TTS subprocess, upgrade Whisper model, add ML intent classification -- all on CPU. |
| When to reconsider? | When adding camera features, or when Hailo's audio/NLP model ecosystem matures. |
| Best bang for buck? | Spend $0 on software improvements (persistent Piper, distil-whisper, ONNX intent classifier). |
| If buying anyway? | Get the 13 TOPS (Hailo-8L) variant -- cheaper, sufficient for any non-LLM workload, lower power. |

---

*Sections 1-7 are based on knowledge current to mid-2025. Section 8 below was added March 2026 with current information.*

---

## 8. AI HAT+ 2 Update (March 2026)

The **Raspberry Pi AI HAT+ 2** was released January 15, 2026 at **$130**. It uses the **Hailo-10H** chip with a fundamentally different value proposition than the original AI HAT+.

### Key Specs

| Spec | AI HAT+ (original) | AI HAT+ 2 |
|---|---|---|
| Chip | Hailo-8L / Hailo-8 | Hailo-10H |
| TOPS | 13 / 26 (INT8) | 40 (INT4) |
| On-board RAM | None (uses system RAM) | **8GB LPDDR4X** (dedicated, invisible to host) |
| Price | $26 / $70 | $130 |
| Power | ~1.5W / ~2.5W | ~2.5W typical, 7.2-7.6W system under LLM load |
| PCIe | Gen 3 x1 | Gen 3 x1 |
| Target workload | Computer vision (CNNs) | **Generative AI (LLMs, VLMs)** |

The 8GB dedicated RAM is the headline change. It means models no longer compete with the Pi's system memory, and the board can work even with a 2GB Pi 5.

### LLM Capabilities — Now Real, But Limited

The AI HAT+ 2 can run LLMs up to **1.5B parameters** via a Hailo-ported version of **Ollama** (compatible with Open WebUI). Available models:

| Model | Parameters | Hailo-10H (tok/s) | Pi 5 CPU only (tok/s) |
|---|---|---|---|
| DeepSeek R1 1.5B | 1.5B | ~6.5 | ~9-10.6 |
| Qwen2 1.5B | 1.5B | ~6.7 | ~9+ |
| Qwen2.5 Coder 1.5B | 1.5B | ~6.7 | ~9+ |
| Llama 3.2 1B | 1B | ~6.5 | ~9+ |

**The uncomfortable truth:** In independent benchmarks (CNX Software, Hackster), the Hailo-10H is **slower than running the same models on the Pi 5 CPU** via llama.cpp/Ollama. The CPU achieves 9-10+ tokens/s while the NPU gets ~6.5-6.7 tokens/s. One reviewer called it "more like an AI decelerator than an AI accelerator."

**However**, the NPU advantage is power efficiency (7.2W vs 10.6W system draw) and freeing the CPU for other tasks. If Bender needs to generate an LLM response while simultaneously listening for a wake word or processing audio, the NPU offload has value.

### What Changed for BenderPi's Workloads

**Local LLM (replacing Claude API):** The assessment partially changes.

- The AI HAT+ 2 CAN run a 1.5B LLM. At ~6.5 tok/s, a 30-token Bender response takes ~4.5s — comparable to the current API round-trip (2-3s network + processing).
- But 1.5B models are **very weak** — reviewers found them failing basic reasoning tasks. The Bender persona prompt would consume a large portion of the context, leaving little room for quality responses.
- The CPU runs the same models faster. So the only reason to use the NPU is CPU offload (keeping the CPU free for wake word + STT).
- **Verdict: Marginal.** A 1.5B model is likely too weak for Bender-quality responses. Claude Haiku remains significantly better at staying in character and answering questions.

**Speech-to-Text (Whisper):** Partially promising.

- Hailo has **demonstrated Whisper running on the Hailo-8** (26 TOPS) accelerator, with the model fitting in on-board memory. The Hailo-10H's 8GB RAM should easily accommodate any Whisper model.
- However, this is **demo-only** — Hailo has not released the pipeline publicly as of March 2026. The demo showed batch processing (not streaming) and "completes in seconds."
- If/when Hailo releases the Whisper pipeline, this could enable running `whisper-small.en` or `whisper-medium.en` on the NPU while keeping the CPU free — a genuine improvement for BenderPi.
- **Verdict: Watch this space.** No actionable pipeline available yet, but this is the most promising future use case.

**TTS (Piper/VITS):** No change. No TTS models in the Hailo model zoo. The persistent Piper subprocess on CPU remains the right approach.

**Intent classification:** No change. Still overkill for a classification task that runs in milliseconds on CPU.

**Computer vision:** Same performance as the original AI HAT+. No improvement. Still the right choice if adding a camera.

### Raspberry Pi's Own Recommendation: Voice-to-Action Agents

The official Raspberry Pi article ["When and why you might need the AI HAT+ 2"](https://www.raspberrypi.com/news/when-and-why-you-might-need-the-raspberry-pi-ai-hat-plus-2/) specifically calls out **local voice-to-action agents** as a strong use case:

> "A strong application of the AI HAT+ 2 is a local voice-to-action agent, combining high-compute inference with relatively low-bandwidth interaction. These workflows often rely on a large prefill step, i.e. processing a big, changing input context before generating a short response."

This describes BenderPi almost exactly — wake word → STT → intent → short response. The key insight is about **prefill performance**: the Hailo-10H is faster at processing large input contexts (prefill) than at generating tokens. For a voice assistant where you process a system prompt + conversation history (large prefill) and generate 1-3 sentences (short generation), the NPU's architecture is less disadvantaged than raw token/sec benchmarks suggest.

However, the independent reviews (CNX Software, Hackster, Jeff Geerling) all found the CPU outperforming the NPU even for these workloads at the 1.5B parameter level. The prefill advantage may only materialise with larger models or longer contexts — and the Hailo-10H's 8GB RAM limit caps model size at 1.5B.

**Revised assessment:** The voice-to-action framing is more favourable than our initial analysis suggested, but the 1.5B model quality constraint remains the blocking issue. If Hailo's model zoo grows to support 3B+ models (or if Whisper becomes available), the value proposition changes significantly.

### Revised Recommendation

| Question | Original Answer | Updated Answer |
|---|---|---|
| Buy AI HAT+ 2 for BenderPi? | N/A | **Not yet.** At $130, the LLM performance doesn't justify the cost — CPU is faster for the same models, and 1.5B models are too weak for quality Bender responses. |
| When to reconsider? | Camera features or ecosystem maturity | **When Hailo releases a public Whisper pipeline.** Running Whisper-medium on the NPU while the CPU handles everything else would be a genuine win. Also if 3B+ models become supported. |
| Better investment? | Software improvements ($0) | Still true. Persistent Piper, better Whisper model on CPU, and the observability improvements in this design spec offer far more value. |
| If buying anyway? | Get the 13 TOPS ($26) | For BenderPi, the **original AI HAT+ 26 TOPS ($70)** is better value IF Hailo's Whisper demo becomes public — it runs on Hailo-8, costs half as much, and doesn't need the LLM RAM. The AI HAT+ 2 only makes sense if you specifically want local LLM capability. |

### Sources

- [Raspberry Pi AI HAT+ 2 product page](https://www.raspberrypi.com/products/ai-hat-plus-2/)
- [When and why you might need the AI HAT+ 2 (official)](https://www.raspberrypi.com/news/when-and-why-you-might-need-the-raspberry-pi-ai-hat-plus-2/)
- [CNX Software review with benchmarks](https://www.cnx-software.com/2026/01/20/raspberry-pi-ai-hat-2-review-a-40-tops-ai-accelerator-tested-with-computer-vision-llm-and-vlm-workloads/)
- [Hackster hands-on review](https://www.hackster.io/news/gen-ai-on-your-raspberry-pi-a-hands-on-review-of-the-raspberry-pi-ai-hat-2-3c829a8894dd)
- [Jeff Geerling review](https://www.jeffgeerling.com/blog/2026/raspberry-pi-ai-hat-2/)
- [Tom's Hardware review (3/5)](https://www.tomshardware.com/raspberry-pi/raspberry-pi-ai-hat-plus-2-review)
- [Hailo Whisper demo on AI HAT+](https://www.hackster.io/news/hailo-demonstrates-accelerated-llm-based-speech-recognition-on-the-raspberry-pi-ai-hat-63eec0214603)
