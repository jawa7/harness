import json
from pathlib import Path


def load_open_task_titles(path, reader=None):
    if reader is None:
        reader = Path.read_text
    payload = json.loads(reader(path))
    return [task["title"] for task in payload["tasks"] if not task["completed"]]
