# -*- coding: utf-8 -*-
"""server/lrucache.py 단위 테스트."""
import threading

import pytest

from server.lrucache import LRUCache


def test_maxsize_must_be_positive():
    with pytest.raises(ValueError):
        LRUCache(maxsize=0)


def test_get_or_create_calls_factory_once_per_key():
    cache: LRUCache = LRUCache(maxsize=4)
    calls = []

    def factory():
        calls.append(1)
        return object()

    a = cache.get_or_create("k", factory)
    b = cache.get_or_create("k", factory)
    assert a is b
    assert len(calls) == 1


def test_evicts_least_recently_used_when_over_capacity():
    cache: LRUCache = LRUCache(maxsize=2)
    cache.get_or_create("a", lambda: "A")
    cache.get_or_create("b", lambda: "B")
    cache.get("a")   # a 를 다시 방문 → 가장 최근 사용은 a, 다음은 b
    cache.get_or_create("c", lambda: "C")   # 상한 초과 → 가장 오래 쓰이지 않은 b 가 밀려남
    assert len(cache) == 2
    assert cache.get("a") == "A"
    assert cache.get("b") is None
    assert cache.get("c") == "C"


def test_cache_size_never_exceeds_maxsize_under_many_keys():
    cache: LRUCache = LRUCache(maxsize=8)
    for i in range(100):
        cache.get_or_create(f"key-{i}", lambda i=i: i)
    assert len(cache) == 8


def test_thread_safety_final_size_bounded():
    cache: LRUCache = LRUCache(maxsize=16)

    def worker(base):
        for i in range(20):
            cache.get_or_create(f"k-{base}-{i}", lambda: object())

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(cache) <= 16
