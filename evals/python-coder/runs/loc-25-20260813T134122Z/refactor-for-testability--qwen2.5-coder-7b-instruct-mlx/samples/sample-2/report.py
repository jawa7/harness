import json


def load_open_task_titles(path, reader=None):
    if reader is None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        payload = json.loads(reader(path))

    return [task["title"] for task in payload["tasks"] if not task["completed"]]