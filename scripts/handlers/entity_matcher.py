"""Pure entity matching — no I/O, no state, synonym-injected at construction."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_NOISE_WORDS = frozenset({
    "light", "lights", "lamp", "lamps", "led", "strip", "switch", "plug",
})

_FILLER = re.compile(
    r"\b(can\s+you|could\s+you|please|put\s+on|set|would\s+you)\b", re.I
)
_ACTION_PHRASES = re.compile(
    r"\b(turn|switch)\s+(on|off)\b", re.I
)
_LONE_DIRECTION = re.compile(r"\b(on|off)\b", re.I)
_PREPOSITIONS = re.compile(r"\b(the|in|to|a|an)\b", re.I)
_TEMPERATURE = re.compile(r"\d+\s*(?:degrees?|°)?")


def normalise(text: str) -> str:
    """Lowercase, strip apostrophes, split CamelCase, replace separators,
    remove noise words, collapse whitespace."""
    text = re.sub(r"'", "", text)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[-_]", " ", text)
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return " ".join(t for t in tokens if t not in _NOISE_WORDS)


def _token_score(term: str, normalised_entity: str) -> float:
    term_tokens = set(term.split())
    entity_tokens = set(normalised_entity.split())
    if not term_tokens or not entity_tokens:
        return 0.0
    overlap = len(term_tokens & entity_tokens)
    return overlap / len(term_tokens)  # divide by user token count, matching original


def _fuzzy_score(term: str, normalised_entity: str) -> float:
    return SequenceMatcher(None, term, normalised_entity).ratio()


class EntityMatcher:
    def __init__(self, synonyms: dict[str, str] | None = None) -> None:
        """synonyms: spoken term → canonical term (e.g. {"office": "study"})."""
        self._synonyms = {k.lower(): v.lower() for k, v in (synonyms or {}).items()}

    def match(
        self,
        term: str,
        entities: list[dict],
        *,
        domain: str | None = None,
        threshold: float = 0.5,
    ) -> list[dict]:
        """Return entities best-matching term, sorted by score descending.

        Two-phase: token scoring first; falls back to fuzzy only if best token
        score is below threshold. Scores against both normalised friendly name
        and normalised entity ID.

        Note: caller must detect pronoun terms (them, it, those, that, these)
        and resolve against last_entities instead of calling this method.
        """
        term_norm = normalise(self._resolve_synonym(term.lower()))
        pool = [e for e in entities if domain is None or e["domain"] == domain]

        # Phase 1 — token scoring
        token_scored: list[tuple[float, dict]] = []
        for entity in pool:
            score = max(
                _token_score(term_norm, entity["normalised"]),
                _token_score(term_norm, entity.get("normalised_id", "")),
            )
            token_scored.append((score, entity))

        best_token = max((s for s, _ in token_scored), default=0.0)

        if best_token >= threshold:
            result = [(s, e) for s, e in token_scored if s >= threshold]
        else:
            # Phase 2 — fuzzy fallback
            result = []
            for _, entity in token_scored:
                score = max(
                    _fuzzy_score(term_norm, entity["normalised"]),
                    _fuzzy_score(term_norm, entity.get("normalised_id", "")),
                )
                if score >= threshold:
                    result.append((score, entity))

        result.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in result]

    @staticmethod
    def parse_action(text: str) -> str | None:
        """Extract 'on' or 'off' from utterance. Returns None if ambiguous."""
        text = text.lower()
        # Explicit compound phrases first (highest confidence)
        if re.search(r"\b(turn\s+on|switch\s+on|enable|activate)\b", text):
            return "on"
        if re.search(r"\b(turn\s+off|switch\s+off|disable|deactivate|kill|cut)\b", text):
            return "off"
        # Bare on/off: "bedroom lights off", "just the office on"
        if re.search(r"\bon\b", text):
            return "on"
        if re.search(r"\boff\b", text):
            return "off"
        return None

    @staticmethod
    def parse_room_term(text: str) -> str | None:
        """Extract room/device noun by stripping action phrases and noise from utterance.

        Returns the residual noun after stripping (may be a pronoun — caller must
        check against PRONOUNS set and resolve via last_entities if so).
        """
        text = text.lower()
        text = _TEMPERATURE.sub("", text)
        text = _FILLER.sub("", text)
        text = _ACTION_PHRASES.sub("", text)
        text = re.sub(r"\b(turn|switch)\b", "", text)
        text = _LONE_DIRECTION.sub("", text)
        text = _PREPOSITIONS.sub("", text)
        for nw in _NOISE_WORDS:
            text = re.sub(rf"\b{nw}\b", "", text)
        term = re.sub(r"\s+", " ", text).strip()
        return term or None

    @staticmethod
    def parse_temperature(text: str) -> float | None:
        """Extract numeric temperature from utterance."""
        m = _TEMPERATURE.search(text)
        if m:
            digits = re.search(r"\d+(?:\.\d+)?", m.group())
            return float(digits.group()) if digits else None
        return None

    def _resolve_synonym(self, term: str) -> str:
        for spoken, canonical in self._synonyms.items():
            if spoken in term:
                term = term.replace(spoken, canonical)
        return term
