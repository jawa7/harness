import pytest
from service import TaskService


def test_task_service_creation():
    service = TaskService()
    assert isinstance(service, TaskService)


def test_task_service_methods():
    service = TaskService()
    # Add tests for the methods of TaskService here
    pass