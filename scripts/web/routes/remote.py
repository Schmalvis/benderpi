import asyncio
import os
import subprocess
import sys

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from web.auth import require_pin

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _SCRIPTS_DIR)

router = APIRouter(dependencies=[Depends(require_pin)])


class _TextQuery(BaseModel):
    text: str


@router.post("/api/remote/ask-text")
async def remote_ask_text(body: _TextQuery):
    """Accept a text query, run it through the response pipeline, return text + WAV as base64."""
    import base64
    import time

    t_start = time.time()
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text query is empty")

    resp_wav = None
    resp_is_temp = False

    try:
        from responder import Responder
        from ai_response import AIResponder
        resp = await asyncio.to_thread(Responder().get_response, text, AIResponder())
        resp_wav = resp.wav_path
        resp_is_temp = resp.is_temp
        resp_text = resp.text
        resp_intent = resp.intent

        with open(resp_wav, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()

        return {
            "transcript": text,
            "response_text": resp_text,
            "intent": resp_intent,
            "audio_b64": audio_b64,
            "duration_ms": round((time.time() - t_start) * 1000),
        }
    finally:
        if resp_is_temp and resp_wav:
            try:
                os.unlink(resp_wav)
            except OSError:
                pass


@router.post("/api/remote/ask")
async def remote_ask(audio: UploadFile = File(...)):
    """Accept audio from browser, transcribe via Whisper, run pipeline, return WAV as base64."""
    import base64
    import tempfile
    import time

    t_start = time.time()
    audio_bytes = await audio.read()
    if len(audio_bytes) < 100:
        raise HTTPException(status_code=400, detail="Audio too short")

    ct = (audio.content_type or "").lower()
    fn = (audio.filename or "").lower()
    if "mp4" in ct or "mp4" in fn or "aac" in ct:
        in_suffix = ".mp4"
    elif "ogg" in ct or "ogg" in fn:
        in_suffix = ".ogg"
    else:
        in_suffix = ".webm"

    tmp_in = tmp_wav = resp_wav = None
    resp_is_temp = False

    try:
        with tempfile.NamedTemporaryFile(suffix=in_suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_in = f.name
        tmp_wav = tmp_in[: -len(in_suffix)] + "_16k.wav"

        conv = await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-y", "-i", tmp_in, "-ar", "16000", "-ac", "1", "-f", "wav", tmp_wav],
            capture_output=True, timeout=30,
        )
        if conv.returncode != 0:
            raise HTTPException(status_code=500, detail="Audio conversion failed")

        import stt as _stt
        # prefer_cpu=True: this is a separate process from bender-converse; force
        # CPU Whisper so we never contend for the shared Hailo STT VDevice.
        transcript = await asyncio.to_thread(_stt.transcribe_file, tmp_wav, prefer_cpu=True)

        if not transcript:
            import tts_generate as _tts
            resp_text = "I heard absolutely nothing. Either speak up or stop wasting my circuits."
            resp_wav = await asyncio.to_thread(_tts.speak, resp_text)
            resp_is_temp = True
            resp_intent = "SILENCE"
        else:
            from responder import Responder
            from ai_response import AIResponder
            resp = await asyncio.to_thread(Responder().get_response, transcript, AIResponder())
            resp_wav = resp.wav_path
            resp_is_temp = resp.is_temp
            resp_text = resp.text
            resp_intent = resp.intent

        with open(resp_wav, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()

        return {
            "transcript": transcript,
            "response_text": resp_text,
            "intent": resp_intent,
            "audio_b64": audio_b64,
            "duration_ms": round((time.time() - t_start) * 1000),
        }

    finally:
        for p in [tmp_in, tmp_wav]:
            try:
                if p:
                    os.unlink(p)
            except OSError:
                pass
        if resp_is_temp and resp_wav:
            try:
                os.unlink(resp_wav)
            except OSError:
                pass
