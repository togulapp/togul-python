from __future__ import annotations

import threading
import time
from typing import Dict, NamedTuple, Optional

from .result import EvaluateResult


class _Entry(NamedTuple):
    result: EvaluateResult
    expires_at: float


class TTLCache:
    """An in-memory, thread-safe cache of evaluation results.

    Eviction is lazy: an entry is dropped when it is read after expiry, or
    when it carries a blank ``value_type`` (a malformed or legacy payload).
    This mirrors the Go, JS and PHP SDKs exactly.
    """

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._store: Dict[str, _Entry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[EvaluateResult]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return None
            if entry.result.value_type == "":
                del self._store[key]
                return None
            return entry.result

    def set(self, key: str, result: EvaluateResult) -> None:
        with self._lock:
            self._store[key] = _Entry(result, time.monotonic() + self._ttl)

    def flush(self) -> None:
        with self._lock:
            self._store.clear()

    def invalidate_flag(self, flag_key: str) -> None:
        prefix = flag_key + ":"
        with self._lock:
            for key in [k for k in self._store if k.startswith(prefix)]:
                del self._store[key]
