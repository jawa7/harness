import pytest
from cli import build_parser, main


def test_build_parser():
    parser = build_parser()
    assert parser is not None


def test_main():
    result = main()
    assert result is not None