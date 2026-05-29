"""Pickle-blob SQLite cache keyed by (namespace, key). TTL is enforced on read."""
from __future__ import annotations

import pickle
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    payload   BLOB NOT NULL,
    fetched_at REAL NOT NULL,
    PRIMARY KEY (namespace, key)
);
"""


class Cache:
    def __init__(self, path: str | Path, ttl_seconds: int):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, namespace: str, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT payload, fetched_at FROM cache WHERE namespace = ? AND key = ?",
            (namespace, key),
        ).fetchone()
        if not row:
            return None
        payload, fetched_at = row
        if time.time() - fetched_at > self.ttl:
            return None
        try:
            return pickle.loads(payload)
        except Exception:
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        blob = pickle.dumps(value)
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (namespace, key, payload, fetched_at) VALUES (?, ?, ?, ?)",
            (namespace, key, blob, time.time()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
