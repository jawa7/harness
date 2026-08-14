from service import TaskService
import pytest

@pytest.fixture
def task_service():
    return TaskService(Storage('test_storage.json'))

@pytest.mark.parametrize("task_id, expected", [(1, True), (2, False)])
def test_complete_task(task_service, task_id, expected):
    task = task_service.complete_task(task_id)
    assert task.completed == expected