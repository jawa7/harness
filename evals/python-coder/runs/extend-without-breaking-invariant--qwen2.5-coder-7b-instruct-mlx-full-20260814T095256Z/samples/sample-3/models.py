class Task:
    def __init__(self, id, project_id, title, description="", tags=None, completed=False):
        self.id = id
        self.project_id = project_id
        self.title = title
        self.description = description
        self.tags = tags if tags is not None else []
        self.completed = completed

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "completed": self.completed
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data.get("id"),
            project_id=data.get("project_id"),
            title=data.get("title"),
            description=data.get("description"),
            tags=data.get("tags", []),
            completed=data.get("completed", False)
        )