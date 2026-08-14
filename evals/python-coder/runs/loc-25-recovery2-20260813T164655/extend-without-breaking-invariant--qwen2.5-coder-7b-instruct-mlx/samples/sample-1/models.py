from uuid import uuid4


class Task:
    def __init__(self, id, project_id, title, description="", tags=None):
        self.id = id
        self.project_id = project_id
        self.title = title
        self.description = description
        self.tags = tags if tags is not None else []

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("id"), data.get("project_id"), data.get("title"), data.get("description", ""), data.get("tags", []))
