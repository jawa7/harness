import pytest

from report import load_open_task_titles


def test_load_open_task_titles_reads_json_file(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text('{"tasks": [{"title": "Write docs", "completed": false}, {"title": "Ship", "completed": true}]}')
    assert load_open_task_titles(path) == ["Write docs"]


def test_load_open_task_titles_accepts_injected_reader():
    seen = []

    def reader(path):
        seen.append(path)
        return '{"tasks": [{"title": "Test me", "completed": false}]}'

    assert load_open_task_titles("ignored.json", reader=reader) == ["Test me"]
    assert seen == ["ignored.json"]
