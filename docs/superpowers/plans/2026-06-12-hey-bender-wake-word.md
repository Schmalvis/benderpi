# Plan: "Hey Bender" Custom Wake Word

**Status:** Ready to execute  
**Replaces:** `hey_jarvis_v0.1.onnx` (interim model)  
**Estimated time:** ~4–6h total, mostly unattended

---

## Overview

openWakeWord supports training custom wake words via synthetic audio generation.
We generate thousands of "hey bender" samples across many TTS voices, mix with
negative samples, train a small classifier, export `.onnx`, deploy.

The Bender Piper TTS model (`models/bender.onnx`) is a bonus: we can generate
positive samples in Bender's own voice, improving accuracy on-device.

---

## Steps

### 1. Set up Colab environment (~15 min)

Open a GPU Colab notebook and clone OWW + install deps:

```bash
git clone https://github.com/dscripka/openWakeWord.git
cd openWakeWord
pip install -r requirements.txt
pip install onnxruntime tensorflow tflite-runtime
```

### 2. Generate positive samples (~1–2h, unattended)

OWW's synthetic pipeline uses Google Cloud TTS (or any local TTS) to generate
thousands of "hey bender" utterances across 100+ voices, speeds, and pitches.

```python
from openwakeword.train import generate_samples

generate_samples(
    target_phrase="hey bender",
    n=5000,              # total samples
    output_dir="data/positive",
    tts="google",        # or "piper" with local binary
    augment=True,        # room impulse response + noise mixing
)
```

**Bonus: Bender's own voice.** On the Pi, generate ~500 extra positive samples
using the Bender Piper model and copy them into `data/positive/`:

```bash
# Run on BenderPi
for i in $(seq 1 500); do
  echo "hey bender" | piper/piper \
    --model models/bender.onnx \
    --output_file data/positive/bender_voice_$i.wav \
    --length-scale $((RANDOM % 4 + 8))e-1  # vary speed 0.8–1.2x
done
```

### 3. Download negative samples (~30 min, unattended)

OWW ships a script to pull MUSAN (music/noise/speech) + Common Voice clips:

```python
from openwakeword.train import download_negative_data

download_negative_data(output_dir="data/negative")
```

### 4. Train the model (~1–2h on Colab GPU)

```python
from openwakeword.train import train_model

train_model(
    positive_dir="data/positive",
    negative_dir="data/negative",
    target_phrase="hey bender",
    output_path="hey_bender_v0.1.onnx",
    epochs=100,
    batch_size=64,
)
```

### 5. Validate

Test the model on a held-out set. Key metrics:
- **False reject rate** — should be <10% on "hey bender" said naturally
- **False accept rate** — should be <1 per hour of background speech
- Run `openwakeword.utils.test_model()` for a quick sanity check

Adjust `oww_threshold` in `bender_config.json` (start at 0.5, tighten if false
accepts are common in a noisy kitchen environment).

### 6. Deploy

```bash
# Copy model to Pi
scp hey_bender_v0.1.onnx pi@192.168.68.132:/home/pi/bender/models/

# Update config (on Pi or via web UI Config tab)
# bender_config.json:
#   "oww_model_path": "models/hey_bender_v0.1.onnx"
#   "oww_threshold": 0.5

sudo systemctl restart bender-converse
```

Watch logs: `sudo journalctl -u bender-converse -f`  
Expected: `Listening for wake word... (model: hey_bender_v0.1.onnx, threshold: 0.50)`

---

## Fallback plan

If accuracy is poor after first training run:
1. Add more Bender-voice positives (steps closer to 1000)
2. Add hard-negative examples — phrases that false-triggered (record from logs)
3. Re-train with `epochs=150`

If "hey bender" is acoustically too close to a common phrase causing false accepts,
consider "okay bender" as an alternative wake phrase (distinct phoneme profile).

---

## Config changes needed post-deploy

In `bender_config.json`:
```json
{
  "oww_model_path": "models/hey_bender_v0.1.onnx",
  "oww_threshold": 0.5
}
```

Update `.gitignore` to keep `models/hey_bender_v0.1.onnx` ignored (large binary).
Update `CLAUDE.md` quirks section: change `hey_jarvis.onnx` reference to `hey_bender_v0.1.onnx`.
