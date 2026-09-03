"""Local LLM responder — Hailo on-chip primary, Ollama CPU fallback."""

import json
import os
import re
import threading
import time

import requests

import hailo_hub
from ai_response import BENDER_SYSTEM_PROMPT
from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("ai_local")

# Soft hedges: in-character for Bender ("I don't know, meatbag!"). A hedge alone
# no longer forces cloud escalation — only a hedge in a *short* reply or a hedge
# that is the *entire* (single-sentence) reply does. See check_response_quality.
HEDGE_PHRASES = {
    "i'm not sure", "i don't know",
    "i cannot", "i can't help", "i'm just a",
    "i apologize",
}

# Hard fails: an assistant breaking character as an AI/LLM is never acceptable,
# regardless of length. These always escalate to cloud.
HARD_FAIL_PHRASES = {
    "as an ai", "language model",
    "i'm not bender", "i am not bender", "i'm an ai", "i'm just a computer",
    # Assistant-style refusals. Spoken live 2026-08-03: "I'm sorry, but I
    # can't assist with that. Please provide a different t…" — the list above
    # had "i can't help" and nothing else that matched.
    "can't assist", "cannot assist", "i'm sorry, but", "i am sorry, but",
    "i'm unable to", "i am unable to", "please provide", "as a chatbot",
    "virtual assistant", "i'm here to help",
}

# Minimum first-sentence length on the stream path. The non-stream rule (10)
# rejected "Yeah." and "No way!" — both in character — and every rejection
# escalates to cloud and wipes the on-chip context.
_STREAM_MIN_CHARS = 3

# A reply this short that also hedges is almost certainly a non-answer worth
# escalating; a longer hedged reply is probably Bender being Bender.
_HEDGE_SHORT_MAX = 40

# Chat-template / tool-calling scaffolding. Their presence means the reply has
# derailed into template output, so it fails the quality gate and escalates.
# Lowercase — matched against the lowercased reply.
_CONTROL_TOKEN_MARKERS = ("<|", "<tool_call>", "</tool_call>", "<think>")

_HAILO_HEF = "/usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef"
_HAILO_RETRY_COOLDOWN = 60  # seconds before retrying after init failure

# Sentence boundary: [.!?] optionally followed by closing quote, then
# either whitespace or end-of-string.
_SENT_RE = re.compile(r'[.!?]["\']?(?:\s|$)')

# Qwen's end-of-turn marker. Verified on-device (2026-07-30): HailoRT yields it
# as its own discrete token rather than splitting it across token boundaries, so
# an equality check is enough — but we also strip it from the buffer in case a
# future runtime chunks differently.
_IM_END = "<|im_end|>"

# A trailing fragment of a special token ("<|im_", "<|") left in the buffer when
# generation stops. Without this, a force-flush would speak the fragment aloud.
_PARTIAL_SPECIAL_RE = re.compile(r'<\|[^>]*$')

# Reply cleaning — applied to every sentence on both stream paths, before the
# quality gate and before the sentence is yielded (so conversation_log records
# what was actually spoken). Each pattern is something Qwen said aloud in
# August 2026 despite the system prompt forbidding it:
#   "(laughter)"                          — parenthetical stage direction
#   "(Bender's casual, slightly rude way…" — meta-commentary in parentheses
#   "\"I'm not going to be the same.\""   — the whole reply wrapped in quotes
#                                          (the model writing a transcript)
_STAGE_DIRECTION_RES = (
    re.compile(r"\((?:laugh|chuckl|giggl|sigh|grin|smirk|pause|snort|bender|robot"
               r"|beat|shrug|wink|sarcas|mutter|whisper|scoff|groan|burp|clank"
               r"|in a |with a )[^)]{0,80}\)", re.IGNORECASE),
    re.compile(r"\[[^\]]{0,40}\]"),          # [laughs], [sighs]
    re.compile(r"\*[^*\n]{1,40}\*"),         # *laughs* — the prompt bans all emotes
)
_SPEAKER_LABEL_RE = re.compile(r"^(?:bender|assistant|robot)\s*:\s*", re.IGNORECASE)
_WRAPPING_QUOTES = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))


def _clean_sentence(sentence: str) -> str:
    """Strip transcript artefacts from one sentence. Returns "" if nothing
    speakable is left (a bare stage direction) — callers skip such sentences."""
    s = sentence.strip()
    s = _SPEAKER_LABEL_RE.sub("", s)
    for rx in _STAGE_DIRECTION_RES:
        s = rx.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for open_q, close_q in _WRAPPING_QUOTES:
        if len(s) >= 2 and s[0] == open_q and s[-1] == close_q:
            s = s[1:-1].strip()
            break
    if not re.search(r"[A-Za-z0-9]", s):
        return ""
    return s


def _flush_sentence(buf: str, force: bool) -> tuple[str, str]:
    """Extract one sentence from ``buf`` if a boundary is present.

    Returns ``(sentence, remainder)``; ``sentence`` is "" when nothing is ready.
    ``force=True`` (generation finished) flushes whatever is left.

    Shared by the Hailo and Ollama streaming paths so sentence segmentation —
    and therefore where TTS starts speaking — is identical on both.
    """
    m = _SENT_RE.search(buf)
    if m:
        return buf[:m.end()].strip(), buf[m.end():]
    if force:
        tail = _PARTIAL_SPECIAL_RE.sub("", buf).strip()
        if tail:
            return tail, ""
    return "", buf


class QualityCheckFailed(Exception):
    """Raised when local LLM response fails quality check."""

    def __init__(self, reason: str, response_text: str):
        self.reason = reason
        self.response_text = response_text
        super().__init__(f"Quality check failed: {reason}")


def check_response_quality(text: str, stream: bool = False) -> tuple[bool, str]:
    """Return (passed, reason). Reason is empty string if passed.

    Softened so an in-character hedge ("I don't know, meatbag!") no longer forces
    a cloud escalation. A soft hedge fails only when the reply is short
    (< _HEDGE_SHORT_MAX chars) or when the hedge is the *only* sentence — i.e.
    the model genuinely produced a non-answer. Hard fails (breaking character as
    an AI / language model) always escalate regardless of length.

    Called on the full reply (non-stream path) and on the first sentence
    (stream path); both cases are covered — first-sentence input naturally
    triggers the single-sentence rule.
    """
    stripped = text.strip()
    min_chars = _STREAM_MIN_CHARS if stream else 10
    if len(stripped) < min_chars:
        return False, "too_short"
    text_lower = stripped.lower()

    # Control-token leakage — the local model has stopped answering and started
    # emitting chat-template scaffolding or a hallucinated next turn. Escalating
    # gets a real answer; tts_generate._sanitize_for_speech would only stop it
    # being *pronounced*, leaving a truncated non-reply. Seen live 2026-08-01
    # (session 5cfc5cd8 turn 4): "<tool_call>", "<|im_start|>user", and a whole
    # invented dialogue, all of which passed this gate and were spoken aloud.
    for marker in _CONTROL_TOKEN_MARKERS:
        if marker in text_lower:
            return False, "control_tokens"

    # Hard fails — always escalate.
    for phrase in HARD_FAIL_PHRASES:
        if phrase in text_lower:
            return False, "hard_fail"

    # Soft hedges — only escalate if the reply is short or the hedge stands alone.
    for phrase in HEDGE_PHRASES:
        if phrase in text_lower:
            is_short = len(stripped) < _HEDGE_SHORT_MAX
            if stream:
                # Only sentence 1 is visible here, so "single sentence" was
                # always true and every in-character hedge escalated. Judge
                # the stream on length alone.
                single_sentence = False
            else:
                single_sentence = len(_SENT_RE.findall(stripped)) <= 1
            if is_short or single_sentence:
                return False, "hedge_phrase"
            # Longer, multi-sentence hedged reply — treat as in-character.
            break
    return True, ""


class _HailoLLMResponder:
    """On-chip LLM using Qwen2.5-1.5B on Hailo-10H. Lazy-initialised."""

    def __init__(self):
        self._vdevice = None
        self._llm = None
        self._available = None  # None = not yet attempted
        self._last_failed_at: float | None = None
        self._scene_context: str = ""
        # HailoRT maintains conversation context ON-CHIP between generate()
        # calls, and rejects a system-role message on any call after the first:
        #   "System role messages can only be provided on the first prompt"
        #   -> HAILO_INVALID_OPERATION(6)
        # So we do NOT keep or resend a message history here (the chip is the
        # history). We only track whether the on-chip context is fresh and how
        # many turns it holds. Before residency this was invisible: the LLM was
        # destroyed and reloaded every turn, so every turn was a fresh context.
        self._context_fresh = True
        self._context_turns = 0
        self._context_dirty = False   # reset requested while a generation was in flight
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
        # Zombie-lock observability: how many consecutive release_chip()/close()
        # calls have been skipped because _infer_lock was held by an in-flight
        # (likely zombie) generate_all(), and when that in-flight call started.
        # A run of skips means a hung generate_all() is stranding the VDevice.
        self._consecutive_release_skips = 0
        self._infer_lock_held_since: float | None = None
        # After this many consecutive skipped releases, emit a hailo_lock_stuck
        # metric + error log so the watchdog surfaces a wedged NPU in STATUS.md.
        self._lock_stuck_threshold = 3

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

        # Resident mode (default): hailo_hub holds the LLM loaded for the life
        # of the process, so this is a cached handle lookup rather than an ~8.4s
        # HEF reload (see the ai_hailo_load metric, which the hub now emits once
        # per process instead of once per AI turn). We borrow the handle; the
        # hub owns teardown, so self._vdevice stays None and release_chip() is
        # a no-op.
        if hailo_hub.enabled():
            llm = hailo_hub.get_llm()
            if llm is None:
                self._available = False
                self._last_failed_at = time.monotonic()
                return False
            self._llm = llm
            self._vdevice = None
            self._available = True
            return True

        if not os.path.exists(_HAILO_HEF):
            log.warning("Hailo LLM HEF not found: %s", _HAILO_HEF)
            self._available = False
            self._last_failed_at = time.monotonic()
            return False
        try:
            # Timed so the per-turn HEF-reload tax is visible in metrics. In
            # per-turn-release mode this fires on *every* AI turn (release_chip()
            # nulls _available after each turn), so ai_hailo_load reveals exactly
            # what a warm session (llm_warm_session=true) would save.
            with metrics.timer("ai_hailo_load"):
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

    def _reset_context(self, *, locked: bool = False) -> None:
        """Wipe the on-chip conversation context so the next prompt may carry a
        system message again. Cheap (no HEF reload) and the only supported way
        to get back to a fresh context.

        ``locked=True`` means the caller already holds ``_infer_lock`` (the
        stream path after a quality failure). Otherwise the lock is taken
        non-blocking: if a zombie generation (a hard-timeout-abandoned thread)
        is still running on this LLM object, clearing the context under it
        would corrupt the inference, so the reset is deferred via
        ``_context_dirty`` and performed by the next ``_build_prompt``.
        """
        if not locked:
            if not self._infer_lock.acquire(blocking=False):
                log.warning("Hailo context reset deferred: a generation is still in flight")
                metrics.count("hailo_context_reset_deferred")
                self._context_dirty = True
                return
        try:
            if self._llm is not None:
                try:
                    self._llm.clear_context()
                except Exception as e:
                    log.warning("Failed to clear Hailo context cache: %s", e)
            self._context_fresh, self._context_turns = True, 0
            self._context_dirty = False
        finally:
            if not locked:
                self._infer_lock.release()

    def _build_prompt(self, user_text: str) -> list:
        """Build the message list for one turn under the on-chip-context rules.

        The system prompt goes in exactly once per context; every later turn
        sends only the new user message, because the chip already holds the
        conversation. That is not just a constraint — it is measurably faster:
        TTFT drops from ~1101ms on the first turn to ~371ms on later ones,
        since the model is no longer re-encoding the whole history each time.

        ``ai_max_history`` still bounds context growth: past that many turns we
        recycle the context rather than let it grow without limit inside a very
        long session.
        """
        if getattr(self, "_context_dirty", False):
            # A reset was requested (session end / gate failure) while a zombie
            # generation held the lock. Do it now, before this turn commits.
            self._reset_context()

        limit = int(getattr(cfg, "ai_max_history", 6))
        if limit > 0 and self._context_turns >= limit:
            log.info("Hailo context reached %d turns — recycling", self._context_turns)
            self._reset_context()

        # Scene context rides on the first user message of the session.
        if self._scene_context and self._context_turns == 0:
            user_text = f"{self._scene_context} {user_text}"

        messages = []
        if self._context_fresh:
            messages.append({
                "role": "system",
                "content": [{"type": "text", "text": BENDER_SYSTEM_PROMPT}],
            })
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": user_text}],
        })
        return messages

    def _acquire_or_fail(self) -> float:
        """Take _infer_lock non-blocking, or raise so the caller fails over.

        A prior generate call (typically a zombie thread abandoned by
        session.py's hard-timeout join) may still be executing on this shared
        _llm object; starting a second concurrent generation would corrupt the
        on-chip context.
        """
        if self._infer_lock.acquire(blocking=False):
            self._infer_lock_held_since = time.monotonic()
            return self._infer_lock_held_since

        held_for = (
            time.monotonic() - self._infer_lock_held_since
            if self._infer_lock_held_since is not None else None
        )
        metrics.count("hailo_busy_lockout",
                      held_seconds=round(held_for, 1) if held_for is not None else None)
        log.warning("Hailo LLM busy (prior generation still in flight, held_for=%s) "
                    "— failing over to Ollama",
                    f"{held_for:.1f}s" if held_for is not None else "unknown")
        # In resident mode release_chip() never runs, so the consecutive-skip
        # counter can no longer detect a wedged NPU. Detect it on elapsed time
        # instead: a generation still in flight past twice the hard timeout is a
        # zombie by definition, since session.py already gave up waiting for it.
        # Keeps hailo_lock_stuck (and the watchdog check reading it) meaningful.
        stuck_after = float(getattr(cfg, "response_hard_timeout_s", 20)) * 2
        if held_for is not None and held_for > stuck_after:
            metrics.count("hailo_lock_stuck", held_seconds=round(held_for, 1),
                          source="generate")
            log.error("Hailo _infer_lock stuck: generation in flight for %.1fs "
                      "(> %.0fs) — NPU likely wedged by a zombie inference",
                      held_for, stuck_after)
        raise RuntimeError("Hailo LLM busy")

    def generate(self, user_text: str) -> str:
        if not self._load():
            raise RuntimeError("Hailo LLM not available")

        messages = self._build_prompt(user_text)

        self._acquire_or_fail()
        try:
            with metrics.timer("ai_hailo_call"):
                result = self._llm.generate_all(
                    prompt=messages,
                    temperature=0.7,
                    seed=42,
                    max_generated_tokens=cfg.ai_max_tokens,
                )
            # The turn is now committed on-chip whatever we do with the text.
            self._context_fresh = False
            self._context_turns += 1
        finally:
            self._infer_lock_held_since = None
            self._infer_lock.release()

        # Strip Qwen special tokens
        reply = result.split(_IM_END)[0].strip() if result else ""

        passed, reason = check_response_quality(reply)
        if not passed:
            # The chip now holds a rejected assistant turn. Escalating to cloud
            # without wiping it would leave that text as context for the next
            # turn, so recycle rather than inherit it.
            self._reset_context()
            raise QualityCheckFailed(reason, reply)

        metrics.count("ai_hailo_success")
        return reply

    def generate_stream(self, user_text: str):
        """Stream the reply sentence-by-sentence as the model produces it.

        This is the real thing, not generate_all() wrapped in a one-item
        generator: HailoRT's LLM.generate() is a context manager yielding
        tokens, so Piper can start speaking sentence 1 while the model is still
        decoding sentence 2. Measured on-device: TTFT 1101ms on a fresh context,
        371ms thereafter, decoding ~5.6-6.9 tok/s (~3 words/s) against a speech
        rate of ~2.5-3 words/s — so generation stays just ahead of playback.

        Only the first sentence is quality-checked: hedges and character breaks
        appear at the start, and once audio is playing we cannot take it back.
        QualityCheckFailed is raised *before* anything is yielded so the caller
        can escalate to cloud cleanly.

        Aborting mid-stream (the caller abandoning this generator) is safe and
        verified on-device: the `with` block closes the completion and the next
        generate() works normally.
        """
        if not self._load():
            raise RuntimeError("Hailo LLM not available")

        messages = self._build_prompt(user_text)

        self._acquire_or_fail()
        started = time.monotonic()
        buffer = ""
        quality_checked = False
        emitted = 0
        try:
            with self._llm.generate(
                prompt=messages,
                temperature=0.7,
                seed=42,
                max_generated_tokens=cfg.ai_max_tokens,
            ) as gen:
                # The turn is committed on-chip the moment generation starts, so
                # mark it before consuming — an abort must not leave us thinking
                # the context is still fresh and resend the system prompt.
                self._context_fresh = False
                self._context_turns += 1

                done = False
                failed: tuple[str, str] | None = None
                for token in gen:
                    if token == _IM_END:
                        done = True
                    else:
                        buffer += token
                        if _IM_END in buffer:
                            buffer = buffer.split(_IM_END)[0]
                            done = True

                    while True:
                        sentence, buffer = _flush_sentence(buffer, force=done)
                        if not sentence:
                            break
                        sentence = _clean_sentence(sentence)
                        if not sentence:
                            continue  # a bare stage direction: nothing to say
                        if not quality_checked:
                            quality_checked = True
                            passed, reason = check_response_quality(sentence, stream=True)
                            if not passed:
                                # Leave the completion first; the reset happens
                                # below, after the `with` has closed it.
                                failed = (reason, sentence)
                                break
                            metrics._write({
                                "type": "timer", "name": "ai_hailo_ttfs",
                                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                            })
                        emitted += 1
                        yield sentence
                    if done or failed is not None:
                        break

                # Anything left when the model stopped without a final boundary.
                if failed is None:
                    sentence, buffer = _flush_sentence(buffer, force=True)
                    sentence = _clean_sentence(sentence) if sentence else ""
                    if sentence:
                        if not quality_checked:
                            quality_checked = True
                            passed, reason = check_response_quality(sentence, stream=True)
                            if not passed:
                                failed = (reason, sentence)
                        if failed is None:
                            emitted += 1
                            yield sentence

            # The completion is closed here and we still hold _infer_lock, so
            # the on-chip reset cannot race a live generation.
            if failed is not None:
                self._reset_context(locked=True)
                raise QualityCheckFailed(*failed)
        finally:
            self._infer_lock_held_since = None
            self._infer_lock.release()

        if emitted:
            metrics.count("ai_hailo_success", streamed=True, sentences=emitted)

    def clear_history(self):
        """Reset for a new session: wipe on-chip context and scene injection."""
        self._scene_context = ""
        self._reset_context()
        log.info("Hailo LLM context cache cleared")

    def reset_state(self) -> None:
        """Clear init-failure cooldown so next _load() retries Hailo immediately."""
        self._available = None
        self._last_failed_at = None

    def release_chip(self, *, warm: bool = False) -> None:
        """Release the Hailo LLM + VDevice between turns/sessions, freeing the
        KV-Cache for STT.

        Uses the public ``.release()`` method (Hailo reference pattern), LLM
        before its VDevice — never ``__exit__()`` + ``del`` + ``gc.collect()``,
        which risks a double-release via ``VDevice.__del__``.

        ``warm=True`` (llm_warm_session mode): this is a *per-turn* call and we
        deliberately do NOT release — the VDevice is held across turns so the
        next AI turn skips the HEF reload. The device is released later by the
        session's ``end()``, which calls with ``warm=False``. NOTE: warm mode
        assumes the Whisper + Qwen HEFs can coexist resident on the Hailo-10H;
        if they cannot, STT will fail on turn 2 (hardware-gated — see docs).

        Guarded by ``_infer_lock``: if a generate_all() call is still in flight
        (typically a zombie thread from a turn whose hard-timeout join gave up),
        this is a no-op — we log a warning and leave the device held rather than
        release it out from under active inference. Taken non-blocking so a hung
        zombie never stalls the conversation loop; the device is simply freed by
        a later turn's release once the zombie finishes. Consecutive skips are
        counted and, past a threshold, emit ``hailo_lock_stuck`` for the
        watchdog — a run of skips means a hung generate_all() has stranded the
        VDevice.
        """
        # Resident mode supersedes warm mode entirely: the chip is held for the
        # life of the process, not the life of a session. (llm_warm_session was
        # the right instinct at the wrong scope — with 1-2 turn sessions it
        # still paid the 8.4s load on the first AI turn of nearly every session.)
        # hailo_hub.close() releases at exit.
        if hailo_hub.enabled():
            metrics.count("hailo_release_skipped", reason="resident")
            return
        if warm:
            # Per-turn call in warm mode — keep the chip resident for the next
            # turn. metrics let us confirm warm mode is actually engaging.
            metrics.count("hailo_release_skipped", reason="warm_session")
            return
        if not self._infer_lock.acquire(blocking=False):
            self._consecutive_release_skips += 1
            held_for = (
                time.monotonic() - self._infer_lock_held_since
                if self._infer_lock_held_since is not None else None
            )
            metrics.count("hailo_release_skipped", reason="infer_in_flight")
            log.warning("Hailo generate_all() still in flight — skipping VDevice "
                        "release to avoid releasing it under active inference "
                        "(consecutive skips=%d, held_for=%s)",
                        self._consecutive_release_skips,
                        f"{held_for:.1f}s" if held_for is not None else "unknown")
            if self._consecutive_release_skips >= self._lock_stuck_threshold:
                metrics.count(
                    "hailo_lock_stuck",
                    skips=self._consecutive_release_skips,
                    held_seconds=round(held_for, 1) if held_for is not None else None,
                )
                log.error("Hailo _infer_lock stuck: %d consecutive release skips, "
                          "generate_all() held for %s — NPU likely wedged by a "
                          "zombie inference; device stranded until it finishes",
                          self._consecutive_release_skips,
                          f"{held_for:.1f}s" if held_for is not None else "unknown")
            return
        try:
            # Clean acquire — any prior zombie has finished; reset skip run.
            self._consecutive_release_skips = 0
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
        if hailo_hub.enabled():
            # The hub owns the models and the VDevice; delegate teardown to it.
            # Still inside the _infer_lock guard above, so we never release the
            # device out from under a live generate call. close() is idempotent,
            # so the hub's own atexit hook firing later is harmless.
            try:
                self._llm = None
                self._vdevice = None
                self._available = None
                self._last_failed_at = None
                hailo_hub.close()
            finally:
                self._infer_lock.release()
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
                        sentence = _clean_sentence(sentence)
                        if not sentence:
                            continue
                        if not quality_checked:
                            quality_checked = True
                            passed, reason = check_response_quality(sentence, stream=True)
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
        """Stream response as sentences. Both backends now stream for real —
        Hailo via HailoRT's token generator, Ollama via its NDJSON stream.

        QualityCheckFailed propagates out — caller decides whether to escalate.

        Ollama failover only applies *before* the first sentence is yielded.
        Once audio is playing we cannot un-speak it and restart on another
        backend, so a mid-stream Hailo failure ends the reply where it stands
        rather than splicing a second model's voice onto the end of the first.
        """
        yielded = False
        try:
            for sentence in self._hailo.generate_stream(user_text):
                yielded = True
                yield sentence
            return
        except QualityCheckFailed:
            raise  # propagate directly — don't try Ollama for quality failures
        except Exception as e:
            if yielded:
                log.warning("Hailo stream failed mid-reply (%s) — ending turn "
                            "early rather than restarting on Ollama", e)
                metrics.count("ai_hailo_stream_truncated", error=str(e)[:120])
                return
            log.info("Hailo unavailable for stream (%s) — falling back to Ollama", e)

        yield from self._ollama.generate_stream(user_text)

    def clear_history(self):
        self._hailo.clear_history()
        self._ollama.clear_history()

    def close(self) -> None:
        """Release all hardware resources. Call on shutdown."""
        self._hailo.close()

    def reset_hailo(self) -> None:
        """Clear Hailo init-failure state so next generate() retries immediately.

        No-op in resident mode: the hub keeps the LLM loaded, so there is no
        per-turn init state to clear, and clearing it would defeat the hub's own
        60s init-retry cooldown — a genuinely dead accelerator would then cost an
        ~8s failed init attempt on every turn instead of once a minute.
        """
        if self._hailo is not None and not hailo_hub.enabled():
            self._hailo.reset_state()

    def release_chip(self, *, warm: bool = False) -> None:
        """Release Hailo VDevice so STT can acquire the KV-Cache.

        ``warm=True`` marks a per-turn call in llm_warm_session mode — the chip
        is kept resident and released later by the session's end() (warm=False).
        """
        if self._hailo is not None:
            self._hailo.release_chip(warm=warm)

    def warm_up(self) -> None:
        """Pre-load Ollama model in background at startup."""
        self._ollama.warm_up()
