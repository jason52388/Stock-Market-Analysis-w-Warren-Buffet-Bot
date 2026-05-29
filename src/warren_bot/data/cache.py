"""Pickle-blob SQLite cache keyed by (namespace, key). TTL is enforced on read.

Thread-safety: a single shared `Cache` instance is safe for concurrent
get/set/prune from multiple threads. The connection is opened with
`check_same_thread=False` and every read/write is serialized through a
threading.Lock, since sqlite3 will otherwise raise from non-owner threads.
"""
from __future__ import annotations

import pickle
import sqlite3
import threading
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
        # check_same_thread=False lets us use one connection from the worker
        # pool; the lock below serializes actual statements.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def get(self, namespace: str, key: str, *, ttl_override: int | None = None) -> Any | None:
        """Read a cached value. Pass ttl_override to use a shorter window than
        the cache's default (e.g. 1h for transient fetch errors vs 1wk for
        clean results)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, fetched_at FROM cache WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        if not row:
            return None
        payload, fetched_at = row
        effective_ttl = ttl_override if ttl_override is not None else self.ttl
        if time.time() - fetched_at > effective_ttl:
            return None
        try:
            return pickle.loads(payload)
        except Exception:
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        blob = pickle.dumps(value)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (namespace, key, payload, fetched_at) VALUES (?, ?, ?, ?)",
                (namespace, key, blob, time.time()),
            )
            self._conn.commit()

    def prune(self, max_age_seconds: int | None = None) -> int:
        """Delete entries older than max_age_seconds (defaults to 2× the
        cache's TTL — old enough that nothing will ever look them up again).
        Returns the number of rows deleted."""
        ceiling = max_age_seconds if max_age_seconds is not None else self.ttl * 2
        cutoff = time.time() - ceiling
        with self._lock:
            cursor = self._conn.execute("DELETE FROM cache WHERE fetched_at < ?", (cutoff,))
            self._conn.commit()
            return cursor.rowcount or 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
