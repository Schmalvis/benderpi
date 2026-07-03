"""Local LLM responder — Hailo on-chip primary, Ollama CPU fallback."""

import json
import os
import re
import threading
import time

import requests

from ai_response import BENDER_SYSTEM_PROMPT
from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("ai_local")

HEDGE_PHRASES = {
    "i'm not sure", "i don't know", "as an ai",
    "i cannot", "i can't help", "i'm just a",
    "language model", "i apologize",
}

_HAILO_HEF = "/usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef"
_HAILO_RETRY_COOLDOWN = 60  # seconds before retrying after init failure

# Sentence boundary: [.!?] optionally followed by closing quote, then
# either whitespace or end-of-string.
_SENT_RE = re.compile(r'[.!?]["\']?(?:\s|$)')


class QualityCheckFailed(Exception):
    """Raised when local LLM response fails quality check."""

    def __init__(self, reason: str, response_text: str):
        self.reason = reason
        self.response_text = response_text
        super().__init__(f"Quality check failed: {reason}")


def check_response_quality(text: str) -> tuple[bool, str]:
    """Return (passed, reason). Reason is empty string if passed."""
    if len(text.strip()) < 10:
        return False, "too_short"
    text_lower = text.lower()
    for phrase in HEDGE_PHRASES:
        if phrase in text_lower:
            return False, "hedge_phrase"
    return True, ""


class _HailoLLMResponder:
    """On-chip LLM using Qwen2.5-1.5B on Hailo-10H. Lazy-initialised."""

    def __init__(self):
        self._vdevice = None
        self._llm = None
        self._available = None  # None = not yet attempted
        self._last_failed_at: float | None = None
        self.history: list[dict] = []
        self._scene_context: str = ""
        # Held for exactly the duration of a self._llm.generate_all() call, from
        # whichever thread issues it. Its lifetime brackets "is the Hailo NPU
        # currently doing LLM inference" independent of caller thread or whether
        # session.py's hard-timeout join already gave up waiting. Used to:
        #   1. stop a new generate() from starting a second concurrent
        #      generate_all() on the shared _llm object (zombie from a timed-out
        #      turn may still be mid-call), and
        #   2. stop release_chip()/close() from releasing the VDevice out from
        #      under a still-running generate_all().
        # Always taken non-blocking so a hung zombie never stalls the loop.
        self._infer_lock = threading.Lock()

    def inject_scene_context(self, text: str):
        """Store scene context to be prepended to the first user message of the session."""
        self._scene_context = text

    def _load(self) -> bool:
        if self._available is True:
            return True
        if self._available is False:
            elapsed = time.monotonic() - (self._last_failed_at or 0.0)
            if elapsed < _HAILO_RETRY_COOLDOWN:
                return False
            log.info("Hailo LLM init cooldown elapsed — retrying")
            self._available = None

        if not os.path.exists(_HAILO_HEF):
            log.warning("Hailo LLM HEF not found: %s", _HAILO_HEF)
            self._available = False
            self._last_failed_at = time.monotonic()
            return False
        try:
            from hailo_platform import VDevice
            from hailo_platform.genai import LLM
            from hailo_apps.python.core.common.defines import SHARED_VDEVICE_GROUP_ID
            params = VDevice.create_params()
            params.group_id = SHARED_VDEVICE_GROUP_ID
            self._vdevice = VDevice(params)
            self._llm = LLM(self._vdevice, _HAILO_HEF)
            self._available = True
            log.info("Hailo LLM ready: Qwen2.5-1.5B on Hailo-10H")
        except Exception as e:
            log.warning("Hailo LLM init failed (%s) — will use Ollama fallback", e)
            self._available = False
            self._last_failed_at = time.monotonic()
        return self._available

    def _trim_history(self):
        if len(self.history) > cfg.ai_max_history * 2:
            self.history = self.history[-(cfg.ai_max_history * 2):]

    def generate(self, user_text: str) -> str:
        if not self._load():
            raise RuntimeError("Hailo LLM not available")

        # Prepend scene context to first user message of the session
        if self._scene_context and len(self.history) == 0:
            user_text = f"{self._scene_context} {user_text}"

        self.history.append({
            "role": "user",
            "content": [{"type": "text", "text": user_text}],
        })

        messages = [
            {"role": "system", "content": [{"type": "text", "text": BENDER_SYSTEM_PROMPT}]},
            *self.history,
        ]

        # A prior generate_all() (e.g. a zombie thread abandoned by session.py's
        # hard-timeout join) may still be executing on this shared _llm object.
        # Acquire non-blocking: if inference is genuinely still in flight, do NOT
        # start a second concurrent generate_all() — roll back the user turn and
        # raise so the caller (LocalAIResponder) fails over to Ollama.
        if not self._infer_lock.acquire(blocking=False):
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            log.warning("Hailo LLM busy (prior generate_all() still in flight) "
                        "— failing over to Ollama")
            raise RuntimeError("Hailo LLM busy")
        try:
            with metrics.timer("ai_hailo_call"):
                result = self._llm.generate_all(
                    prompt=messages,
                    temperature=0.7,
                    seed=42,
                    max_generated_tokens=cfg.ai_max_tokens,
                )
        finally:
            self._infer_lock.release()

        # Strip Qwen special tokens
        reply = result.split("<|im_end|>")[0].strip() if result else ""

        self.history.append({
            "role": "assistant",
            "content": [{"type": "text", "text": reply}],
        })
        self._trim_history()

        passed, reason = check_response_quality(reply)
        if not passed:
            raise QualityCheckFailed(reason, reply)

        metrics.count("ai_hailo_success")
        return reply

    def clear_history(self):
        self.history = []
        self._scene_context = ""
        if self._llm is not None:
            try:
                self._llm.clear_context()
                log.info("Hailo LLM context cache cleared")
            except Exception as e:
                log.warning("Failed to clear Hailo context cache: %s", e)

    def reset_state(self) -> None:
        """Clear init-failure cooldown so next _load() retries Hailo immediately."""
        self._available = None
        self._last_failed_at = None

    def release_chip(self) -> None:
        """Release the Hailo LLM + VDevice between sessions, freeing the KV-Cache
        for STT.

        Uses the public ``.release()`` method (Hailo reference pattern), LLM
        before its VDevice — never ``__exit__()`` + ``del`` + ``gc.collect()``,
        which risks a double-release via ``VDevice.__del__``.

        Guarded by ``_infer_lock``: if a generate_all() call is still in flight
        (typically a zombie thread from a turn whose hard-timeout join gave up),
        this is a no-op — we log a warning and leave the device held rather than
        release it out from under active inference. Taken non-blocking so a hung
        zombie never stalls the conversation loop; the device is simply freed by
        a later turn's release once the zombie finishes.
        """
        if not self._infer_lock.acquire(blocking=False):
            log.warning("Hailo generate_all() still in flight — skipping VDevice "
                        "release to avoid releasing it under active inference")
            return
        try:
            llm_ref, vdev_ref = self._llm, self._vdevice
            self._llm = None
            self._vdevice = None
            self._available = None
            if llm_ref is not None:
                try:
                    llm_ref.clear_context()
                except Exception as e:
                    log.debug("Hailo LLM clear_context error: %s", e)
                try:
                    llm_ref.release()
                except Exception as e:
                    log.debug("Hailo LLM release error: %s", e)
            if vdev_ref is not None:
                try:
                    vdev_ref.release()
                except Exception as e:
                    log.debug("Hailo LLM VDevice release error: %s", e)
            log.debug("Hailo LLM chip released (will re-acquire on next generate)")
        finally:
            self._infer_lock.release()

    def close(self) -> None:
        """Release Hailo LLM + VDevice, freeing the on-chip KV-Cache. Called at
        process exit (atexit). Uses the public ``.release()`` method (LLM before
        its VDevice) rather than bare ``del``, so teardown is deterministic and
        matches Hailo's reference pattern.

        Like release_chip(), this respects _infer_lock: if a generate_all() is
        still in flight we skip the hardware release (only null our refs) rather
        than release under active inference — the OS reclaims the device handle
        on process death. Taken non-blocking so exit never hangs on a zombie."""
        if not self._infer_lock.acquire(blocking=False):
            log.warning("Hailo generate_all() still in flight at close() — "
                        "skipping hardware release; OS will reclaim on exit")
            self._llm = None
            self._vdevice = None
            self._available = None
            self._last_failed_at = None
            return
        try:
            llm_ref, vdev_ref = self._llm, self._vdevice
            self._llm = None
            self._vdevice = None
            self._available = None
            self._last_failed_at = None
            if llm_ref is not None:
                try:
                    llm_ref.clear_context()
                except Exception:
                    pass
                try:
                    llm_ref.release()
                except Exception:
                    pass
            if vdev_ref is not None:
                try:
                    vdev_ref.release()
                except Exception:
                    pass
            log.info("Hailo LLM closed and KV-Cache released")
        finally:
            self._infer_lock.release()


class _OllamaResponder:
    """CPU fallback via Ollama REST API."""

    def __init__(self):
        self.history: list[dict] = []
        self._scene_context: str = ""

    def inject_scene_context(self, text: str):
        """Store scene context to be prepended to the first user message of the session."""
        self._scene_context = text

    def _trim_history(self):
        if len(self.history) > cfg.ai_max_history * 2:
            self.history = self.history[-(cfg.ai_max_history * 2):]

    def generate(self, user_text: str) -> str:
        # Prepend scene context to first user message of the session
        if self._scene_context and len(self.history) == 0:
            user_text = f"{self._scene_context} {user_text}"

        self.history.append({"role": "user", "content": user_text})

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
                    "options": {"num_predict": cfg.ai_max_tokens},
                },
                timeout=cfg.local_llm_timeout,
            )
            resp.raise_for_status()

        reply = resp.json()["message"]["content"].strip()
        self.history.append({"role": "assistant", "content": reply})
        self._trim_history()

        passed, reason = check_response_quality(reply)
        if not passed:
            raise QualityCheckFailed(reason, reply)

        metrics.count("ai_local_success")
        return reply

    def generate_stream(self, user_text: str):
        """Stream response as sentences. Yields each sentence as Piper can start it.

        Quality-checks the first sentence only — hedge phrases nearly always
        appear at the start of the response. Raises QualityCheckFailed (before
        yielding anything) if the first sentence fails. Caller must handle the
        exception and escalate to cloud.

        History is appended to self.history only after the generator is fully
        consumed. If abandoned mid-stream, partial history is discarded.
        """
        if self._scene_context and len(self.history) == 0:
            user_text = f"{self._scene_context} {user_text}"

        self.history.append({"role": "user", "content": user_text})

        buffer = ""
        collected: list[str] = []
        quality_checked = False

        def _flush_sentence(buf: str, force: bool) -> tuple[str, str]:
            """Extract one sentence from buf if boundary found (or force=True).
            Returns (sentence, remainder)."""
            m = _SENT_RE.search(buf)
            if m:
                sent = buf[:m.end()].strip()
                rest = buf[m.end():]
                return sent, rest
            if force and buf.strip():
                return buf.strip(), ""
            return "", buf

        try:
            with requests.post(
                f"{cfg.local_llm_url}/api/chat",
                json={
                    "model": cfg.local_llm_model,
                    "messages": [
                        {"role": "system", "content": BENDER_SYSTEM_PROMPT},
                        *self.history,
                    ],
                    "stream": True,
                    "options": {"num_predict": cfg.ai_max_tokens},
                },
                stream=True,
                timeout=cfg.local_llm_timeout,
            ) as resp:
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    buffer += chunk.get("message", {}).get("content", "")
                    done = chunk.get("done", False)

                    # Flush as many complete sentences as possible
                    while True:
                        sentence, buffer = _flush_sentence(buffer, force=done)
                        if not sentence:
                            break
                        if not quality_checked:
                            quality_checked = True
                            passed, reason = check_response_quality(sentence)
                            if not passed:
                                raise QualityCheckFailed(reason, sentence)
                        collected.append(sentence)
                        yield sentence

        except QualityCheckFailed:
            # Roll back user turn — caller will retry with cloud
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            raise
        except Exception:
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            raise
        else:
            # Generator fully consumed without exception — commit to history
            if collected:
                self.history.append({"role": "assistant", "content": " ".join(collected)})
                self._trim_history()
                metrics.count("ai_local_success")
            else:
                # Empty stream — undo user message
                if self.history and self.history[-1].get("role") == "user":
                    self.history.pop()

    def clear_history(self):
        self.history = []
        self._scene_context = ""

    def warm_up(self) -> None:
        """Pre-load the Ollama model so first real request doesn't cold-start."""
        try:
            requests.post(
                f"{cfg.local_llm_url}/api/chat",
                json={
                    "model": cfg.local_llm_model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                    "options": {"num_predict": 1},
                },
                timeout=30,
            )
            log.info("Ollama model pre-loaded (%s)", cfg.local_llm_model)
        except Exception as e:
            log.warning("Ollama warm-up failed (non-fatal): %s", e)


class LocalAIResponder:
    """Local LLM — Hailo on-chip primary, Ollama CPU fallback."""

    def __init__(self):
        self._hailo = _HailoLLMResponder()
        self._ollama = _OllamaResponder()

    def inject_scene_context(self, text: str):
        """Delegate scene context to both underlying responders."""
        self._hailo.inject_scene_context(text)
        self._ollama.inject_scene_context(text)

    def generate(self, user_text: str) -> str:
        """Try Hailo first; fall back to Ollama on hardware unavailability only.

        QualityCheckFailed is NOT caught here — it propagates up so the
        responder can escalate directly to cloud without a 3s Ollama timeout.
        """
        try:
            return self._hailo.generate(user_text)
        except QualityCheckFailed:
            raise  # let responder handle cloud escalation
        except RuntimeError:
            log.info("Hailo LLM unavailable — falling back to Ollama")
            return self._ollama.generate(user_text)
        except Exception as e:
            log.warning("Hailo LLM error (%s) — falling back to Ollama", e)
            return self._ollama.generate(user_text)

    def generate_stream(self, user_text: str):
        """Stream response as sentences. Hailo wraps full text as one item;
        Ollama truly streams tokens into sentences as they form.

        QualityCheckFailed propagates out — caller decides whether to escalate.
        """
        try:
            # Hailo doesn't expose token-level streaming; generate fully then yield once.
            text = self._hailo.generate(user_text)
            yield text
            return
        except QualityCheckFailed:
            raise  # propagate directly — don't try Ollama for quality failures
        except Exception as e:
            log.info("Hailo unavailable for stream (%s) — falling back to Ollama", e)

        yield from self._ollama.generate_stream(user_text)

    def clear_history(self):
        self._hailo.clear_history()
        self._ollama.clear_history()

    def close(self) -> None:
        """Release all hardware resources. Call on shutdown."""
        self._hailo.close()

    def reset_hailo(self) -> None:
        """Clear Hailo init-failure state so next generate() retries immediately."""
        if self._hailo is not None:
            self._hailo.reset_state()

    def release_chip(self) -> None:
        """Release Hailo VDevice so STT can acquire the KV-Cache."""
        if self._hailo is not None:
            self._hailo.release_chip()

    def warm_up(self) -> None:
        """Pre-load Ollama model in background at startup."""
        self._ollama.warm_up()
