#!/usr/bin/env python3
"""Verify the refactor's tests are active, not merely expected to fail."""
import subprocess
import sys
from pathlib import Path


result = subprocess.run([sys.executable, "-m", "pytest", "-q"])
if result.returncode:
    raise SystemExit(result.returncode)
if "pytest.mark.xfail" in Path("tests/test_report.py").read_text(encoding="utf-8"):
    print("the injected-reader test must be active rather than xfailed", file=sys.stderr)
    raise SystemExit(1)
