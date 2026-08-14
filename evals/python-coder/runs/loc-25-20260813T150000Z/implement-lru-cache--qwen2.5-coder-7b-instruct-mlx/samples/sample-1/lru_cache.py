class LRUCache:
    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.capacity = capacity
        self.cache = {}
        self.order = []

    def get(self, key: str) -> int:
        if key not in self.cache:
            return None
        else:
            # Move the accessed key to the end of the order list
            self.order.remove(key)
            self.order.append(key)
            return self.cache[key]

    def put(self, key: str, value: int) -> None:
        if key in self.cache:
            # Move the updated key to the end of the order list
            self.order.remove(key)
        elif len(self.cache) >= self.capacity:
            # Evict the least recently used key
            lru_key = self.order.pop(0)
            del self.cache[lru_key]
        # Add the new key to the cache and order list
        self.cache[key] = value
        self.order.append(key)