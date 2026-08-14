from storage import Storage
import pytest

@pytest.fixture
def storage():
    return Storage('test_storage.json')

@pytest.mark.parametrize("task_id, changes, expected", [(1, {"completed": True}, True), (2, {"title": "Updated Title"}, "Updated Title")])
def test_update(storage, task_id, changes, expected):
    storage.create(Task(task_id, 1, "Original Title", "Description"))
    updated_task = storage.update(task_id, changes)
    assert updated_task.completed == expected