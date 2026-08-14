import pytest
from service import TaskService, create_task


def test_create_task():
    task_service = TaskService(None)
    task = create_task(1, 'test task')
    assert task