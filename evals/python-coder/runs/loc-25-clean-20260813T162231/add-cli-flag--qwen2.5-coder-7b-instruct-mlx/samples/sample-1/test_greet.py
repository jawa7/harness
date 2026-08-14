import argparse
from unittest.mock import patch

def test_greet_no_shout():
    with patch('builtins.print') as mock_print:
        import greet
        greet.greet("Alice")
        mock_print.assert_called_once_with('Hello, Alice!')


def test_greet_shout():
    with patch('builtins.print') as mock_print:
        import greet
        greet.greet("Alice", args=argparse.Namespace(shout=True))
        mock_print.assert_called_once_with('HELLO, ALICE!')