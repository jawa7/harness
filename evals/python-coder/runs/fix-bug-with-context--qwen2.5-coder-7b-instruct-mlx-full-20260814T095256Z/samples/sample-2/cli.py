import argparse


def build_parser():
    parser = argparse.ArgumentParser(prog="tasktracker")
    parser.add_argument("create", nargs="?", help="create a task with this title")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.create:
        print(f"create: {args.create}")
