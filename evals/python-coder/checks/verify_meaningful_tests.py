#!/usr/bin/env python3
"""Case-local verifier for write-tests-for-untested-module.

Coverage alone doesn't prove a test checks anything: a test that calls every line
without a single assertion satisfies `--cov-fail-under=100` while testing nothing
(demonstrated for real against this exact fixture -- a try/except-wrapped call with
no assert reached 100% coverage and a passing exit code). Require every test
function to contain at least one real assertion before trusting the coverage
number at all.
"""
import ast
import subprocess
import sys
from pathlib import Path


def _has_assertion(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            # A bare literal ("assert True", "assert 1") can never depend on the code
            # under test's actual behaviour -- it's the cheapest possible way to satisfy
            # "has an assert" while checking nothing. Everything else (a comparison, a
            # call result, an attribute) counts as a real check.
            if not isinstance(child.test, ast.Constant):
                return True
            continue
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr.startswith("assert"):
                return True
            if child.func.attr in ("raises", "warns"):
                return True
    return False


empty = []
for path in sorted(Path(".").glob("test_*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_") and not _has_assertion(node):
            empty.append(f"{path}::{node.name}")
if empty:
    print(
        "test function(s) call code under test but make no assertion (would pass "
        "trivially regardless of correctness): " + ", ".join(empty),
        file=sys.stderr,
    )
    raise SystemExit(1)

result = subprocess.run([sys.executable, "-m", "pytest", "-q", "--cov=pricing", "--cov-fail-under=100"])
raise SystemExit(result.returncode)
