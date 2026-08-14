import pytest
from service import TaskService, create_task


def test_create_task_with_tags():
    task_service = TaskService(None)
    task = create_task(1, 'test task', tags=['tag1', 'tag2'])
    assert task.tags == ['tag1', 'tag2']