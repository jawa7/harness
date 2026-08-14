import pytest

from errors import NotFoundError
from service import TaskService
from storage import Storage


def test_archive_task_returns_dataclass_and_removes_it_from_rollup(tmp_path):
    service = TaskService(Storage(tmp_path / "tasks.json"))
    task = service.create_task(9, "Retire me")
    archived = service.archive_task(task.id)
    assert archived.id == task.id
    assert archived.archived is True
    assert archived.__class__.__name__ == "Task"
    assert service.project_rollup(9) == {"total": 0, "completed": 0}


def test_archive_task_uses_not_found_error(tmp_path):
    service = TaskService(Storage(tmp_path / "tasks.json"))
    with pytest.raises(NotFoundError):
        service.archive_task(404)
