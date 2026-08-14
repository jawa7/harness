import pytest
from pyfixture_tasktracker.service import TaskService, NotFoundError
from pyfixture_tasktracker.models import Task


@pytest.fixture
def task_service(storage):
    return TaskService(storage)



def test_archive_task(task_service, storage):
    task = task_service.create_task(1, "Test Task")
    archived_task = task_service.archive_task(task.id)
    assert archived_task.archived is True
    with pytest.raises(NotFoundError):
        task_service.get(task.id)