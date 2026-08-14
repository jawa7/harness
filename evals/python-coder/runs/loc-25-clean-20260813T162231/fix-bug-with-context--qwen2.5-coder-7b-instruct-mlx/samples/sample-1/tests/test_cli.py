import pytest
from cli import build_parser, main


def test_build_parser():
    parser = build_parser()
    assert 'task' in parser._actions


def test_main():
    # Add tests for the main function here
    pass