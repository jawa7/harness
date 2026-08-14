import collections

class LRUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.cache = collections.OrderedDict()
        self.capacity = capacity

    def get(self, key):
        if key not in self.cache:
            return None
        else:
            # Move the accessed item to the end to mark it as recently used
            self.cache.move_to_end(key)
            return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            # Move the updated item to the end
            self.cache.move_to_end(key)
        elif len(self.cache) >= self.capacity:
            # Evict the least recently used item
            self.cache.popitem(last=False)
        self.cache[key] = value