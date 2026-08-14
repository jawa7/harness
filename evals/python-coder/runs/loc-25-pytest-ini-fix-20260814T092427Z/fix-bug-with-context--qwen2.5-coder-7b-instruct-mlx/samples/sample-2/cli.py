import argparse
from service import TaskService
from storage import Storage


def build_parser():
    parser = argparse.ArgumentParser(prog="tasktracker")
    parser.add_argument("create", nargs="?", help="create a task with this title")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    storage = Storage("tasks.json")
    task_service = TaskService(storage)

    if args.create:
        try:
            task = task_service.create_task(None, args.create)
            print(f"created task {task.id}")
        except ValidationError as e:
            print(e)

if __name__ == "__main__":
    main()