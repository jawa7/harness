import pytest

from errors import ValidationError
from service import TaskService
from storage import Storage


def service(tmp_path):
    return TaskService(Storage(tmp_path / "tasks.json"))


def test_create_complete_and_roll_up_tasks(tmp_path):
    tasks = service(tmp_path)
    first = tasks.create_task(1, "Write docs")
    tasks.create_task(1, "Ship release")
    assert tasks.complete_task(first.id).completed is True
    assert tasks.project_rollup(1) == {"total": 2, "completed": 1}


def test_rejects_blank_titles(tmp_path):
    with pytest.raises(ValidationError):
        service(tmp_path).create_task(1, "  ")


def test_create_task_with_tags(tmp_path):
    tasks = service(tmp_path)
    task = tasks.create_task(1, "Write tests", tags=["bugfix"])
    assert task.tags == ["bugfix"]


def test_create_task_without_tags(tmp_path):
    tasks = service(tmp_path)
    task = tasks.create_task(1, "Write tests")
    assert task.tags == []