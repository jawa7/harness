import argparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument('--shout', action='store_true')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    greeting = f"Hello, {args.name}!"
    if args.shout:
        greeting = greeting.upper()
    print(greeting)


if __name__ == "__main__":
    main()
