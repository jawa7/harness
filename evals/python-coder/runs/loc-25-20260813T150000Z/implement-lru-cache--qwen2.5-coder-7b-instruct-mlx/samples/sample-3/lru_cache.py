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

    def __repr__(self):
        return f"LRUCache({self.cache}, {self.order})"