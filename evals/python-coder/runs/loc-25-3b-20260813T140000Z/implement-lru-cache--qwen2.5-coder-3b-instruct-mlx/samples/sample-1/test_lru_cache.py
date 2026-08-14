import pytest

from lru_cache import LRUCache


def test_get_missing_value_and_eviction_order():
    cache = LRUCache(2)
    assert cache.get("missing") is None
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_put_replaces_and_marks_recent():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 10)
    cache.put("c", 3)
    assert cache.get("a") == 10
    assert cache.get("b") is None


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        LRUCache(0)
