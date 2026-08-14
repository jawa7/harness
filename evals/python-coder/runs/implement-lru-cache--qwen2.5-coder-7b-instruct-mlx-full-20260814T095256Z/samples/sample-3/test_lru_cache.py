import unittest
from lru_cache import LRUCache

class TestLRUCache(unittest.TestCase):
    def test_get_put(self):
        cache = LRUCache(2)
        self.assertEqual(cache.get(1), None)
        cache.put(1, 1)
        self.assertEqual(cache.get(1), 1)
        cache.put(2, 2)
        self.assertEqual(cache.get(2), 2)
        self.assertEqual(cache.get(1), 1)

    def test_eviction(self):
        cache = LRUCache(2)
        cache.put(1, 1)
        cache.put(2, 2)
        cache.put(3, 3)
        self.assertEqual(cache.get(1), None)
        cache.put(4, 4)
        self.assertEqual(cache.get(2), None)

    def test_capacity(self):
        with self.assertRaises(ValueError):
            LRUCache(0)

if __name__ == "__main__":
    unittest.main()