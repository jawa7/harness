#!/usr/bin/env python3
"""Fast contract checks for Python Coder fixture discovery and sample seeding."""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import qwen_agent


SCRIPT = Path(__file__).parent / "evals" / "python-coder" / "run_case.py"
SPEC = importlib.util.spec_from_file_location("python_coder_run_case", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

SUMMARY_SCRIPT = Path(__file__).parent / "evals" / "python-coder" / "summarize.py"
SUMMARY_SPEC = importlib.util.spec_from_file_location("python_coder_summarize", SUMMARY_SCRIPT)
summarizer = importlib.util.module_from_spec(SUMMARY_SPEC)
SUMMARY_SPEC.loader.exec_module(summarizer)


def write_native_artifact(runs_dir, case_id, model, samples):
    """Write the smallest valid, pre-scored native harness artifact."""
    run_dir = runs_dir / f"{case_id}--{summarizer.safe_model_name(model)}"
    rows = []
    for number, sample in enumerate(samples, 1):
        workdir = f"samples/sample-{number}"
        log_path = run_dir / workdir / "harness-log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps({"turns": sample["turns"], "crashed": sample.get("crashed")}))
        rows.append({
            "sample": number,
            "workdir": workdir,
            "passed": sample.get("passed", False),
            "crashed": sample.get("crashed"),
        })
    summary_path = run_dir / "harness-log-samples.json"
    summary_path.write_text(json.dumps({
        "samples": rows,
        "passed_count": sum(row["passed"] for row in rows),
        "total": len(rows),
    }))
    return summary_path


def native_turn(*calls):
    return {"turn": 1, "response": {"tool_calls": [
        {"function": {"name": name, "arguments": arguments}}
        for name, arguments in calls
    ]}}


def test_python_coder_eval():
    expected = {
        "implement-lru-cache", "fix-off-by-one-bug", "add-cli-flag", "refactor-for-testability",
        "write-tests-for-untested-module", "follow-existing-pattern", "fix-bug-with-context",
        "extend-without-breaking-invariant",
    }
    assert set(runner.CASES) == expected
    assert [runner.CASES[name]["kind"] for name in runner.CASES][-3:] == ["repo-context"] * 3
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "run"
        runner.seed_samples("follow-existing-pattern", root, 2)
        sample = root / "samples" / "sample-1"
        assert (sample / "errors.py").is_file()
        assert (sample / "logging_utils.py").is_file()
        assert (sample / "tests" / "test_archive_task.py").is_file()
        assert (root / "samples" / "sample-2" / "service.py").is_file()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "run"
        runner.seed_samples("fix-bug-with-context", root, 1)
        assert "partial update" in (root / "samples/sample-1/storage.py").read_text()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "run"
        runner.seed_samples("extend-without-breaking-invariant", root, 1)
        assert "tags" not in (root / "samples/sample-1/models.py").read_text()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "run"
        runner.seed_samples("add-cli-flag", root, 1)
        # This representative fixture intentionally puts its test at repository
        # root, not under tests/; the coding runner opts into bootstrap context.
        sample = root / "samples/sample-1"
        assert (sample / "test_greet.py").is_file()
        assert "test_greet.py" in qwen_agent.build_workspace_bootstrap(sample)
    command = runner.command_for("follow-existing-pattern", "qwen2.5-coder-7b-instruct-mlx", 3, Path("/tmp/run"), "http://localhost:1234/v1")
    assert "--samples" in command and "--verify-command" in command
    assert "--bootstrap-workspace-context" in command
    assert "--bootstrap-include-verify-command" in command
    assert "--recovery-turns" not in command
    recovery_command = runner.command_for("follow-existing-pattern", "qwen2.5-coder-7b-instruct-mlx", 3, Path("/tmp/run"), "http://localhost:1234/v1", recovery_turns=2)
    assert recovery_command[-2:] == ["--recovery-turns", "2"]
    # --enable-web-access is opt-in (off by default): these fixtures are synthetic
    # and stdlib-only, so there is nothing to look up unless explicitly requested.
    assert "--enable-web-access" not in command
    web_command = runner.command_for("follow-existing-pattern", "qwen2.5-coder-7b-instruct-mlx", 3, Path("/tmp/run"), "http://localhost:1234/v1", enable_web_access=True)
    assert "--enable-web-access" in web_command
    assert "--enable-run-command" in web_command, "web access must not replace run_command"
    assert any("verify_archive_task.py" in part for part in command)
    assert runner.sys.executable in command
    environment = runner.harness_environment()
    assert environment["PATH"].split(os.pathsep)[0] == str(Path(runner.sys.executable).parent)
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir = Path(tmp)
        for case_id in runner.CASES:
            write_native_artifact(runs_dir, case_id, "qwen/test", [
                {"passed": True, "turns": [native_turn(("write_file", '{"path":"work.py","content":"x"}'))]},
                {"passed": True, "turns": [native_turn(("write_file", '{"path":"work.py","content":"y"}'))]},
                {"turns": [native_turn(("write_file", '{"path":"work.py","content":"z"}'))], "crashed": "--verify-command failed: exit 1"},
            ])
        rows = summarizer.summarize(runs_dir, ["qwen/test"])
        assert rows[0]["slices"] == {
            "overall": {"passed": 16, "total": 24, "pass_rate": 16 / 24, "failure_categories": {"prose-only": 0, "no-edits": 0, "repeated-command": 0, "verifier-failure": 8, "timeout": 0, "tool-error": 0}},
            "isolated": {"passed": 10, "total": 15, "pass_rate": 10 / 15, "failure_categories": {"prose-only": 0, "no-edits": 0, "repeated-command": 0, "verifier-failure": 5, "timeout": 0, "tool-error": 0}},
            "repo-context": {"passed": 6, "total": 9, "pass_rate": 6 / 9, "failure_categories": {"prose-only": 0, "no-edits": 0, "repeated-command": 0, "verifier-failure": 3, "timeout": 0, "tool-error": 0}},
        }
        report = summarizer.render_markdown(rows)
        assert "Repo-context" in report
        assert "harness-log-samples.json" in report
        manifest = summarizer.comparison_manifest(runs_dir, rows, ["--max-turns=16"], "fixture-123")
        assert manifest == {
            "format": "python-coder-comparison-v1",
            "models": ["qwen/test"],
            "samples": {"qwen/test": {case_id: 3 for case_id in runner.CASES}},
            "harness_flags": ["--max-turns=16"],
            "fixture_revision": "fixture-123",
            "output_root": str(runs_dir.resolve()),
        }
    categories = {
        "prose-only": {"turns": [native_turn()]},
        "no-edits": {"turns": [native_turn(("read_file", '{"path":"work.py"}'))]},
        "repeated-command": {"turns": [native_turn(("run_command", '{"command":"pytest -q"}'), ("run_command", '{"command":"pytest -q"}'))]},
        "verifier-failure": {"turns": [native_turn(("write_file", '{"path":"work.py","content":"x"}'))], "crashed": "--verify-command failed: exit 1"},
        "timeout": {"turns": [native_turn(("write_file", '{"path":"work.py","content":"x"}'))], "crashed": "command timed out after 60s"},
        "tool-error": {"turns": [native_turn(("write_file", '{"path":"work.py","content":"x"}'))], "crashed": "ValueError: bad tool arguments"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir = Path(tmp)
        for category, sample in categories.items():
            summary_path = write_native_artifact(runs_dir, category, "model", [sample])
            case = summarizer.read_case_artifact(runs_dir, "model", category, "isolated")
            assert case["failure_categories"][category] == 1
            assert case["artifact"] == summary_path.relative_to(runs_dir).as_posix()
            assert case["sample_artifacts"][0]["artifact"].endswith("samples/sample-1/harness-log.json")
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir = Path(tmp)
        try:
            summarizer.read_case_artifact(runs_dir, "model", "missing", "isolated")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("missing artifact was accepted")
        path = write_native_artifact(runs_dir, "malformed", "model", [{"turns": []}])
        path.write_text("not JSON")
        try:
            summarizer.read_case_artifact(runs_dir, "model", "malformed", "isolated")
        except ValueError:
            pass
        else:
            raise AssertionError("malformed artifact was accepted")
        path = write_native_artifact(runs_dir, "missing-log", "model", [{"turns": []}])
        log_path = path.parent / "samples/sample-1/harness-log.json"
        log_path.unlink()
        try:
            summarizer.read_case_artifact(runs_dir, "model", "missing-log", "isolated")
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("missing sample log was accepted")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("[]")
        try:
            summarizer.read_case_artifact(runs_dir, "model", "missing-log", "isolated")
        except ValueError:
            pass
        else:
            raise AssertionError("malformed sample log was accepted")
    # Bug fix: an outright API/harness crash before any model turn completed
    # (e.g. HTTPError from a context-length-exceeded request — turns: [] in the
    # artifact) must not be mislabeled as prose-only just because there are no
    # tool calls to inspect either way; it's a request failure, not the model
    # faking an edit in prose.
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir = Path(tmp)
        write_native_artifact(runs_dir, "crash-before-turns", "model", [
            {"turns": [], "crashed": "HTTPError: HTTP Error 400: Bad Request"},
        ])
        case = summarizer.read_case_artifact(runs_dir, "model", "crash-before-turns", "isolated")
        assert case["failure_categories"]["tool-error"] == 1, case["failure_categories"]
        assert case["failure_categories"]["prose-only"] == 0, case["failure_categories"]
    print("all checks passed")


if __name__ == "__main__":
    test_python_coder_eval()
