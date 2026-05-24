"""HA entity discovery with TTL cache."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import get_logger

from .entity_matcher import normalise
from .ha_client import HAClient

log = get_logger("entity_registry")

_CONTROLLABLE_DOMAINS = frozenset({"light", "switch", "climate"})


class EntityRegistry:
    def __init__(
        self,
        client: HAClient,
        *,
        exclude_keywords: set[str] | None = None,
        exclude_entities: set[str] | None = None,
        ttl_s: float = 60.0,
    ) -> None:
        self._client = client
        self._exclude_kw = {k.lower() for k in (exclude_keywords or set())}
        self._exclude_ids = set(exclude_entities or set())
        self._ttl = ttl_s
        self._cache: list[dict] = []
        self._cache_ts: float = 0.0

    def get(self) -> list[dict]:
        """Return filtered entity list, refreshing from HA when TTL expires."""
        if time.monotonic() - self._cache_ts > self._ttl:
            self._cache = self._fetch()
            self._cache_ts = time.monotonic()
        return self._cache

    def invalidate(self) -> None:
        self._cache_ts = 0.0

    def _fetch(self) -> list[dict]:
        try:
            states = self._client.get_states()
        except Exception as exc:
            log.warning("HA entity fetch failed (%s) — returning stale cache", exc)
            return self._cache

        result = []
        for s in states:
            eid = s.get("entity_id", "")
            domain = eid.split(".")[0]
            if domain not in _CONTROLLABLE_DOMAINS:
                continue
            if s.get("state") in ("unavailable", "unknown"):
                continue
            if eid in self._exclude_ids:
                continue
            friendly = s.get("attributes", {}).get("friendly_name", eid)
            # Check exclude keywords against entity ID and friendly name
            eid_lower = eid.lower()
            friendly_lower = friendly.lower()
            if any(kw in eid_lower for kw in self._exclude_kw):
                continue
            if any(kw in friendly_lower for kw in self._exclude_kw):
                continue
            result.append({
                "entity_id": eid,
                "domain": domain,
                "friendly_name": friendly,
                "normalised": normalise(friendly),
                "normalised_id": normalise(eid.replace(".", " ")),
                "state": s.get("state"),
                "attributes": s.get("attributes", {}),
            })
        return result
