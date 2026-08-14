import pytest
from service import TaskService, Storage
from models import Task


def test_create_task_with_tags():
    storage = Storage()
    task_service = TaskService(storage)
    project_id = "project1"
    title = "Task 1"
    description = "Description of task 1"
    tags = ["tag1", "tag2"]
    task = task_service.create_task(project_id, title, description, tags)
    assert task.tags == tags


def test_create_task_without_tags():
    storage = Storage()
    task_service = TaskService(storage)
    project_id = "project1"
    title = "Task 2"
    description = "Description of task 2"
    task = task_service.create_task(project_id, title, description)
    assert task.tags == []
