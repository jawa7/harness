from dataclasses import dataclass, field

@dataclass
class Task:
    id: str = None
    project_id: int = 0
    title: str = ""
    description: str = ""
    completed: bool = False
    archived: bool = False
    tags: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "completed": self.completed,
            "archived": self.archived,
            "tags": self.tags
        }