"""Tests for the SQLite cache — basic behavior, TTL, transient override, thread safety."""
from __future__ import annotations

import threading
import time

import pytest


class TestBasic:
    def test_set_then_get_roundtrip(self, tmp_cache):
        tmp_cache.set("snapshot", "AAPL", {"value": 42, "extra": [1, 2, 3]})
        result = tmp_cache.get("snapshot", "AAPL")
        assert result == {"value": 42, "extra": [1, 2, 3]}

    def test_missing_key_returns_none(self, tmp_cache):
        assert tmp_cache.get("snapshot", "DOESNOTEXIST") is None

    def test_namespace_isolation(self, tmp_cache):
        tmp_cache.set("snapshot", "AAPL", "ticker-data")
        tmp_cache.set("dataroma", "AAPL", "hedge-data")
        assert tmp_cache.get("snapshot", "AAPL") == "ticker-data"
        assert tmp_cache.get("dataroma", "AAPL") == "hedge-data"

    def test_replace_overwrites(self, tmp_cache):
        tmp_cache.set("ns", "k", "v1")
        tmp_cache.set("ns", "k", "v2")
        assert tmp_cache.get("ns", "k") == "v2"


class TestTTL:
    def test_ttl_expiry(self, tmp_path):
        """A value set then read past its TTL returns None."""
        from warren_bot.data.cache import Cache

        cache = Cache(tmp_path / "ttl.sqlite", ttl_seconds=0.01)
        cache.set("ns", "k", "v")
        time.sleep(0.05)
        assert cache.get("ns", "k") is None
        cache.close()

    def test_ttl_override_shorter(self, tmp_path):
        """A shorter override expires entries that the default TTL would still serve."""
        from warren_bot.data.cache import Cache

        cache = Cache(tmp_path / "override.sqlite", ttl_seconds=3600)
        cache.set("ns", "k", "v")
        time.sleep(0.05)
        # Default TTL says fresh
        assert cache.get("ns", "k") == "v"
        # Override says expired
        assert cache.get("ns", "k", ttl_override=0.01) is None
        cache.close()


class TestPrune:
    def test_prune_removes_old_entries(self, tmp_path):
        from warren_bot.data.cache import Cache

        cache = Cache(tmp_path / "prune.sqlite", ttl_seconds=1)
        cache.set("ns", "old1", "v1")
        cache.set("ns", "old2", "v2")
        time.sleep(0.1)
        cache.set("ns", "fresh", "v3")
        # Prune anything older than 0.05s — should remove old1, old2 but keep fresh
        removed = cache.prune(max_age_seconds=0.05)
        assert removed == 2
        assert cache.get("ns", "old1", ttl_override=999) is None
        assert cache.get("ns", "fresh", ttl_override=999) == "v3"
        cache.close()

    def test_prune_default_uses_2x_ttl(self, tmp_path):
        from warren_bot.data.cache import Cache

        # ttl=0.05s → default prune cutoff = 0.10s
        cache = Cache(tmp_path / "default_prune.sqlite", ttl_seconds=0.05)
        cache.set("ns", "k", "v")
        time.sleep(0.15)
        removed = cache.prune()
        assert removed == 1
        cache.close()


class TestThreadSafety:
    """Regression test for the new parallel fetcher — Cache must tolerate
    concurrent reads/writes from multiple threads without raising or losing data."""

    def test_concurrent_writes_dont_raise(self, tmp_cache):
        errors: list[BaseException] = []

        def worker(worker_id: int):
            try:
                for i in range(50):
                    tmp_cache.set("ns", f"w{worker_id}-{i}", i)
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"thread errors: {errors}"

    def test_concurrent_writes_all_persist(self, tmp_cache):
        def worker(worker_id: int):
            for i in range(20):
                tmp_cache.set("ns", f"w{worker_id}-{i}", {"w": worker_id, "i": i})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 4×20 = 80 entries should be present
        for w in range(4):
            for i in range(20):
                assert tmp_cache.get("ns", f"w{w}-{i}") == {"w": w, "i": i}

    def test_concurrent_read_write_mix(self, tmp_cache):
        """Readers and writers don't deadlock or corrupt data."""
        tmp_cache.set("ns", "key", 0)
        stop = threading.Event()
        errors: list[BaseException] = []

        def writer():
            try:
                v = 0
                while not stop.is_set():
                    v += 1
                    tmp_cache.set("ns", "key", v)
            except BaseException as e:
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    val = tmp_cache.get("ns", "key")
                    assert val is None or isinstance(val, int)
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=writer)] + \
                  [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        stop.set()
        for t in threads:
            t.join()
        assert errors == [], f"thread errors: {errors}"
