from models import Task
from storage import Storage


def test_storage_assigns_monotonically_increasing_ids_and_round_trips(tmp_path):
    path = tmp_path / "tasks.json"
    store = Storage(path)
    first = store.create(Task(99, 1, "first"))
    second = store.create(Task(None, 1, "second", "details"))
    assert (first.id, second.id) == (1, 2)
    reopened = Storage(path)
    assert reopened.get(2).to_dict() == Task.from_dict(second.to_dict()).to_dict()
    assert reopened.create(Task(None, 1, "third")).id == 3