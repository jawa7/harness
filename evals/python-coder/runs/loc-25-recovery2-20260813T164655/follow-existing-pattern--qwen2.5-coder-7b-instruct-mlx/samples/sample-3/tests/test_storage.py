import sys
sys.path.insert(0, '..')
from storage import Storage
from models import Task

import pytest


def test_storage():
    storage = Storage('./test_storage.db')
    task = Task(None, 'project1', 'task1')
    storage.create(task)
    retrieved_task = storage.get(task.id)
    assert retrieved_task is not None


def test_bulk_import():
    storage = Storage('./test_storage.db')
    tasks = [{'project_id': 'project1', 'title': 'task1'}, {'project_id': 'project2', 'title': 'task2'}]
    imported_tasks = storage.bulk_import(tasks)
    assert len(imported_tasks) == 2