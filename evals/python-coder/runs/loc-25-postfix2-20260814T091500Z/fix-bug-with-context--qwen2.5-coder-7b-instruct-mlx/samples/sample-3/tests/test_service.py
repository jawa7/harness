from service import TaskService
import pytest


def test_task_service():
    service = TaskService()
    assert isinstance(service, TaskService)