import json
from pathlib import Path


def load_open_task_titles(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [task["title"] for task in payload["tasks"] if not task["completed"]]
