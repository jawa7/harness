import pytest
from report import load_open_task_titles
import os


def test_load_open_task_titles():
    mock_content = '{"tasks": [{"title": "Task 1", "completed": false}, {"title": "Task 2", "completed": true}]}'
    with open('mock_tasks.json', 'w') as file:
        file.write(mock_content)

    try:
        assert load_open_task_titles('mock_tasks.json') == ["Task 1"]
    finally:
        os.remove('mock_tasks.json')