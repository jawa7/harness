import argparse
from unittest.mock import patch

@patch('argparse.ArgumentParser.parse_args', return_value=argparse.Namespace(name='World'))
def test_greet_normal():
    import greet
    assert 'Hello, World!' in greet.__main__

@patch('argparse.ArgumentParser.parse_args', return_value=argparse.Namespace(name='World', shout=True))
def test_greet_shout():
    import greet
    assert 'HELLO, WORLD!' in greet.__main__