from service import TaskService
from storage import Storage


def test_create_task_persists_optional_tags_and_defaults_to_new_empty_lists(tmp_path):
    path = tmp_path / "tasks.json"
    service = TaskService(Storage(path))
    tagged = service.create_task(2, "Labelled", tags=["urgent", "backend"])
    plain_one = service.create_task(2, "Plain one")
    plain_two = service.create_task(2, "Plain two")
    assert tagged.tags == ["urgent", "backend"]
    assert Storage(path).get(tagged.id).tags == ["urgent", "backend"]
    assert plain_one.tags == []
    assert plain_two.tags == []
    assert plain_one.tags is not plain_two.tags
