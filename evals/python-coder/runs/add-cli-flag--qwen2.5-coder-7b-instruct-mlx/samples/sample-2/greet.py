import argparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    print(f"Hello, {args.name}!")


if __name__ == "__main__":
    main()
