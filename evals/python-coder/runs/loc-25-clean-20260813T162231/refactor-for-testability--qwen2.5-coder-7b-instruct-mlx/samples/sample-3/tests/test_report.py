import json
from report import load_open_task_titles

def test_load_open_task_titles():
    # Mock tasks.json content
    with open('tasks.json', 'w') as f:
        json.dump([
            {'title': 'Task 1', 'completed': False},
            {'title': 'Task 2', 'completed': True}
        ], f)

    expected_titles = ['Task 1']
    assert load_open_task_titles('tasks.json') == expected_titles

# Removing xfail marker to make the test pass