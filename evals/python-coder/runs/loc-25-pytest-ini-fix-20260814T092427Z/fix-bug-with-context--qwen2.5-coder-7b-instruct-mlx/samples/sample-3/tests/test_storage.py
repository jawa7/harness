import pytest
from models import Task
from storage import Storage

@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "tasks.json"))

def test_update_partial_changes(storage):
    task = Task(id=1, project_id=1, title="Old Title", description="Old Description")
    storage.create(task)

    updated_task = Task(id=1, project_id=1, title="New Title")
    storage.update(1, updated_task.to_dict())

    stored_task = storage.get(1)
    assert stored_task.title == "New Title"
    assert stored_task.description == "Old Description"