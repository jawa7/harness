import sys
sys.path.insert(0, '..')
from errors import NotFoundError
from service import TaskService
from models import Task

import pytest


def test_archive_task():
    task_service = TaskService(None)
    with pytest.raises(NotFoundError):
        task_service.archive_task('nonexistent_task_id')

    task = Task(None, 'project1', 'task1')
    archived_task = task_service.archive_task(task.id)
    assert archived_task.archived