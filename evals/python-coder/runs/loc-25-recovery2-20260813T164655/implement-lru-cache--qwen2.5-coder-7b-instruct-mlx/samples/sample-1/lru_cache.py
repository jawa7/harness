class LRUCache:
    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        self.cache = {}
        self.capacity = capacity
        self.order = []

    def get(self, key: str) -> int:
        if key not in self.cache:
            return None
        else:
            # Move the accessed item to the end of the order list
            self.order.remove(key)
            self.order.append(key)
            return self.cache[key]

    def put(self, key: str, value: int) -> None:
        if key in self.cache:
            # Move the updated item to the end of the order list
            self.order.remove(key)
        else:
            if len(self.cache) >= self.capacity:
                # Remove the least recently used item
                lru_key = self.order.pop(0)
                del self.cache[lru_key]
        # Add the new item to the cache and order list
        self.cache[key] = value
        self.order.append(key)