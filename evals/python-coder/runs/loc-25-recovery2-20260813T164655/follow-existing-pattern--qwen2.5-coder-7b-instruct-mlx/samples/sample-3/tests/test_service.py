import sys
sys.path.insert(0, '..')
from service import TaskService
from models import Task

import pytest


def test_create_task():
    task_service = TaskService(None)
    task = task_service.create_task('project1', 'task1')
    assert task is not None


def test_complete_task():
    task_service = TaskService(None)
    task = task_service.create_task('project1', 'task1')
    completed_task = task_service.complete_task(task.id)
    assert completed_task.completed