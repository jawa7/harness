import argparse
from service import TaskService
from storage import Storage


def build_parser():
    parser = argparse.ArgumentParser(prog="tasktracker")
    parser.add_argument("create", nargs="?", help="create a task with this title")
    return parser


def test_cli_parses_create_title(capsys):
    storage = Storage("tasks.json")
    task_service = TaskService(storage)
    main(["Write docs"])
    captured = capsys.readouterr()
    assert 'created task' in captured.out

if __name__ == "__main__":
    main()