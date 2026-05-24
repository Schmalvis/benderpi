"""HA REST API adapter — injectable for testing."""

from __future__ import annotations

import json
import urllib.request
from typing import Protocol, runtime_checkable


@runtime_checkable
class HAClient(Protocol):
    def get_states(self) -> list[dict]: ...
    def call(self, domain: str, service: str, entity_id: str, extra: dict | None = None) -> bool: ...


class UrllibHAClient:
    def __init__(self, ha_url: str, ha_token: str, *, timeout_s: int = 10) -> None:
        self._url = ha_url.rstrip("/")
        self._auth = {"Authorization": f"Bearer {ha_token}"}
        self._json = {**self._auth, "Content-Type": "application/json"}
        self._timeout = timeout_s

    def get_states(self) -> list[dict]:
        req = urllib.request.Request(
            f"{self._url}/api/states",
            headers=self._auth,
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read())

    def call(self, domain: str, service: str, entity_id: str, extra: dict | None = None) -> bool:
        payload = {"entity_id": entity_id, **(extra or {})}
        req = urllib.request.Request(
            f"{self._url}/api/services/{domain}/{service}",
            data=json.dumps(payload).encode(),
            headers=self._json,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return r.status in (200, 201)
        except Exception:
            return False
