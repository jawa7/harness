from cli import build_parser, main
import pytest

@pytest.mark.parametrize("args", [([], 1), (['--version'], 0)])
def test_build_parser(args):
    parser = build_parser()
    retcode = main(args)
    assert retcode == args[1]