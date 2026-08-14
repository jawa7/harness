class Task:
    def __init__(self, id=None):
        self.id = id

    @classmethod
    def from_dict(cls, data):
        return cls(id=data.get('id'))

    def to_dict(self):
        return {'id': self.id}
