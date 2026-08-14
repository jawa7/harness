from models import Task
from service import TaskService


def test_complete_task_keeps_the_task_fields_its_caller_did_not_send(tmp_path):
    service = TaskService(Storage(tmp_path / "tasks.json"))
    task = service.create_task(3, "Preserve me", "context matters")
    completed = service.complete_task(task.id)
    assert completed.completed is True
    assert completed.project_id == 3
    assert completed.title == "Preserve me"
    assert completed.description == "context matters"


def test_bulk_import_still_replaces_all_fields_for_existing_task(tmp_path):
    service = TaskService(Storage(tmp_path / "tasks.json"))
    task = service.create_task(3, "Old", "old description")
    updated = service.bulk_import([{
        "id": task.id, "project_id": 4, "title": "New", "description": "new description",
        "completed": True, "archived": False,
    }])[0]
    assert updated.to_dict() == {
        "id": task.id, "project_id": 4, "title": "New", "description": "new description",
        "completed": True, "archived": False,
    }
