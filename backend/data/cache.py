"""Small on-disk cache for expensive provider calls.

Cached payloads are pickled pandas/py objects keyed by (namespace, key). Each entry
stores the time it was written so the caller can report data freshness honestly.
The cache never fabricates data: a miss simply returns None.
"""

from __future__ import annotations

import hashlib
import pickle
import time
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled
        if enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self.root / namespace / f"{h}.pkl"

    def get(self, namespace: str, key: str, ttl_hours: float) -> tuple[Any, float] | None:
        """Return (value, written_at_epoch) if fresh, else None."""
        if not self.enabled:
            return None
        p = self._path(namespace, key)
        if not p.exists():
            return None
        try:
            with p.open("rb") as f:
                written_at, value = pickle.load(f)
        except Exception:
            return None
        if time.time() - written_at > ttl_hours * 3600:
            return None
        return value, written_at

    def set(self, namespace: str, key: str, value: Any) -> None:
        if not self.enabled:
            return
        p = self._path(namespace, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with tmp.open("wb") as f:
            pickle.dump((time.time(), value), f)
        tmp.replace(p)
