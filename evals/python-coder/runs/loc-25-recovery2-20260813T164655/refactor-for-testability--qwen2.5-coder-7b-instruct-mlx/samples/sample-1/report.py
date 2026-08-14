import json


def load_open_task_titles(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        payload = json.load(file)
    return [task["title"] for task in payload["tasks"] if not task["completed"]]