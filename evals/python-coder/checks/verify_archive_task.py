#!/usr/bin/env python3
"""Case-local deterministic verifier for the archive-task convention check."""
import subprocess
import sys
from pathlib import Path


result = subprocess.run([sys.executable, "-m", "pytest", "-q"])
if result.returncode:
    raise SystemExit(result.returncode)
source = Path("service.py").read_text(encoding="utf-8")
required = ("from errors import NotFoundError", "from logging_utils import get_logger", "get_logger(__name__)")
missing = [item for item in required if item not in source]
if missing:
    print("archive_task must reuse established errors.py/logging_utils.py conventions: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
if "def archive_task" not in source or "logger.info" not in source.split("def archive_task", 1)[1]:
    print("archive_task must log through the established task-tracker logger", file=sys.stderr)
    raise SystemExit(1)
