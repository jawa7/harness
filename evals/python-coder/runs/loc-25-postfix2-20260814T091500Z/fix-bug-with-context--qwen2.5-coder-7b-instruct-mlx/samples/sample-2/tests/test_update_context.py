import pytest
from service import TaskService, ValidationError
from models import Task


def test_update_context():
    storage = Storage(path="test_storage.json")
    service = TaskService(storage)
    task = service.create_task(project_id=1, title="Test Task")
    updated_task = service.complete_task(task.id)
    assert updated_task is not None
    assert updated_task.completed