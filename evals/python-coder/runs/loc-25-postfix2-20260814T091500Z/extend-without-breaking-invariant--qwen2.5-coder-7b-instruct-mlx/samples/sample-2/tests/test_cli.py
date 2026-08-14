import pytest
from cli import build_parser, main


def test_build_parser():
    parser = build_parser()
    assert parser


def test_main():
    result = main()
    assert result