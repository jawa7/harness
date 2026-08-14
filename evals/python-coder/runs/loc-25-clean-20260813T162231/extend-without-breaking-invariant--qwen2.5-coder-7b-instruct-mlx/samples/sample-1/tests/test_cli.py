from cli import build_parser, main
from models import Task
from service import TaskService


def test_cli_parses_create_title(capsys):
    main(["Write docs"])
    assert capsys.readouterr().out == "create: Write docs\n"
    assert build_parser().parse_args([]).create is None
