import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("greet.py")


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True, check=True).stdout


def test_default_greeting_is_unchanged():
    assert run("Ada") == "Hello, Ada!\n"


def test_shout_flag_uppercases_the_complete_greeting():
    assert run("--shout", "Ada") == "HELLO, ADA!\n"
