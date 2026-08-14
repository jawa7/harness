import pytest
from service import TaskService, ValidationError
from models import Task


def test_create_task():
    storage = Storage(path="test_storage.json")
    service = TaskService(storage)
    task = service.create_task(project_id=1, title="Test Task")
    assert task is not None
    assert task.project_id == 1
    assert task.title == "Test Task"
    assert not task.completed


def test_complete_task():
    storage = Storage(path="test_storage.json")
    service = TaskService(storage)
    task = service.create_task(project_id=1, title="Test Task")
    completed_task = service.complete_task(task.id)
    assert completed_task is not None
    assert completed_task.completed


def test_bulk_import():
    storage = Storage(path="test_storage.json")
    service = TaskService(storage)
    tasks = [Task(id=None, project_id=1, title="Test Task 1", completed=False), Task(id=None, project_id=2, title="Test Task 2", completed=False)]
    imported_tasks = service.bulk_import(tasks)
    assert len(imported_tasks) == 2
    for task in imported_tasks:
        assert task.project_id is not None
        assert task.title is not None
        assert task.completed == False