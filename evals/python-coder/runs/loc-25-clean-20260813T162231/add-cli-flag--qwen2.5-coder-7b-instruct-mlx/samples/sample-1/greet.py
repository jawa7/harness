import argparse

def greet(name):
    print(f'Hello, {name}!')

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Greet someone")
    parser.add_argument("name", help="Name to greet")
    args = parser.parse_args()
    if args.shout:
        print(f'HELLO, {args.name.upper()}!')
    else:
        greet(args.name)
