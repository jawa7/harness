from dataclasses import asdict, dataclass
from typing import List


@dataclass
class Task:
    id: int | None
    project_id: int
    title: str
    description: str = ""
    completed: bool = False
    archived: bool = False
tags: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data.get("id"), project_id=data["project_id"], title=data["title"],
            description=data.get("description", ""), completed=data.get("completed", False),
            archived=data.get("archived", False),
            tags=data.get("tags", []),
        )


@dataclass
class Project:
    id: int
    name: str
