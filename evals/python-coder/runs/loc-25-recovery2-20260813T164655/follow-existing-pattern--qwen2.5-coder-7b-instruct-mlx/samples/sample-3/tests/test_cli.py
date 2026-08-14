import sys
sys.path.insert(0, '..')
from cli import build_parser, main
from errors import ValidationError

import pytest


def test_build_parser():
    parser = build_parser()
    assert parser is not None


def test_main():
    with pytest.raises(ValidationError):
        main(['--task-id', 'nonexistent_task_id'])