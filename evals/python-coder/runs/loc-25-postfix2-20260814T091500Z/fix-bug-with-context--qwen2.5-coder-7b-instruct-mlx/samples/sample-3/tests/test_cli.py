from cli import build_parser, main
import pytest


def test_build_parser():
    parser = build_parser()
    assert 'task' in parser._actions


def test_main():
    result = main()
    assert result == 0