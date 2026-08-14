import pytest

from service import Task, TaskService

def test_create_task_with_tags(tmp_path):
    db_path = tmp_path / 'test.db'
    storage = Storage(db_path)
    task_service = TaskService(storage)

    task_id = '1'
    description = 'Test task with tags'
    tags = ['tag1', 'tag2']

    task_service.create_task(task_id, description, tags)

    with open(db_path, 'r') as f:
        for line in f:
            task_data = json.loads(line)
            if task_data['task_id'] == task_id:
                assert task_data['description'] == description
                assert task_data['tags'] == tags

def test_create_task_without_tags(tmp_path):
    db_path = tmp_path / 'test.db'
    storage = Storage(db_path)
    task_service = TaskService(storage)

    task_id = '2'
    description = 'Test task without tags'

    task_service.create_task(task_id, description)

    with open(db_path, 'r') as f:
        for line in f:
            task_data = json.loads(line)
            if task_data['task_id'] == task_id:
                assert task_data['description'] == description
                assert task_data['tags'] == []