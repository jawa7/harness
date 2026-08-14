class LRUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key):
        if key not in self.cache:
            return None
        else:
            self._move_to_end(key)
            return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self._move_to_end(key)
        elif len(self.cache) >= self.capacity:
            lru_key = self.order.pop(0)
            del self.cache[lru_key]
        self.cache[key] = value
        self._move_to_end(key)

    def _move_to_end(self, key):
        if key in self.order:
            self.order.remove(key)
        self.order.append(key)

# Example usage
if __name__ == "__main__":
    cache = LRUCache(2)
    cache.put(1, 1)
    cache.put(2, 2)
    print(cache.get(1))       # returns 1
    cache.put(3, 3)           # evicts key 2
    print(cache.get(2))       # returns None (not found)
    cache.put(4, 4)           # evicts key 1
    print(cache.get(1))       # returns None (not found)
    print(cache.get(3))       # returns 3
    print(cache.get(4))       # returns 4