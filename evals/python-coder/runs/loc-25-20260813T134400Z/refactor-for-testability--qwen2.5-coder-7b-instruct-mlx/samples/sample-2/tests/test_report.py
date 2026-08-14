import json
from unittest.mock import patch

def test_load_open_task_titles():
    tasks = [
        {'title': 'Task 1', 'completed': False},
        {'title': 'Task 2', 'completed': True}
    ]

    with patch('report.read_file') as mock_read_file:
        mock_read_file.return_value = json.dumps(tasks)
        open_tasks = report.load_open_task_titles()

    assert open_tasks == [{'title': 'Task 1', 'completed': False}]
