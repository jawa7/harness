import json
from pathlib import Path

from errors import NotFoundError
from models import Task


class Storage:
    """Small JSON-backed task store; this layer alone assigns task IDs."""

    def __init__(self, path):
        self.path = Path(path)
        self.tasks = {}
        self.next_task_id = 1
        if self.path.exists():
            self._load()

    def _load(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.next_task_id = data["next_task_id"]
        self.tasks = {task["id"]: Task.from_dict(task) for task in data["tasks"]}

    def save(self):
        payload = {"next_task_id": self.next_task_id, "tasks": [task.to_dict() for task in self.tasks.values()]}
        self.path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def create(self, task):
        task.id = self.next_task_id
        self.next_task_id += 1
        self.tasks[task.id] = task
        self.save()
        return task

    def get(self, task_id):
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise NotFoundError(f"task {task_id} does not exist") from exc

    def update(self, task_id, changes):
        task = self.get(task_id)
        updated_data = {**task.to_dict(), **changes}
        updated_task = Task.from_dict(updated_data)
        self.tasks[task_id] = updated_task
        self.save()
        return updated_task
