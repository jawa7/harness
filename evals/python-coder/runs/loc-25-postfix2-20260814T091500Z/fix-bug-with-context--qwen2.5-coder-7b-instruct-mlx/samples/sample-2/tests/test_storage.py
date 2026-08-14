import pytest
from storage import Storage, NotFoundError
from models import Task


def test_create():
    storage = Storage(path="test_storage.json")
    task = Task(id=None, project_id=1, title="Test Task", completed=False)
    created_task = storage.create(task)
    assert created_task is not None
    assert created_task.id == 1
    assert created_task.project_id == 1
    assert created_task.title == "Test Task"
    assert not created_task.completed


def test_get():
    storage = Storage(path="test_storage.json")
    task = Task(id=None, project_id=1, title="Test Task", completed=False)
    created_task = storage.create(task)
    retrieved_task = storage.get(created_task.id)
    assert retrieved_task is not None
    assert retrieved_task.id == created_task.id
    assert retrieved_task.project_id == created_task.project_id
    assert retrieved_task.title == created_task.title
    assert not retrieved_task.completed


def test_update():
    storage = Storage(path="test_storage.json")
    task = Task(id=None, project_id=1, title="Test Task", completed=False)
    created_task = storage.create(task)
    updated_task = storage.update(created_task.id, {"completed": True})
    assert updated_task is not None
    assert updated_task.id == created_task.id
    assert updated_task.project_id == created_task.project_id
    assert updated_task.title == created_task.title
    assert updated_task.completed