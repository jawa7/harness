#!/usr/bin/env python3
"""Aggregate existing native Python Coder harness artifacts.

The summarizer deliberately does not invoke a model, verifier, or scorer.  It
only reads ``harness-log-samples.json`` and the per-sample ``harness-log.json``
files that the native harness already wrote.
"""
import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CASES = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))

PASS = "pass"
PROSE_ONLY = "prose-only"
NO_EDITS = "no-edits"
REPEATED_COMMAND = "repeated-command"
VERIFIER_FAILURE = "verifier-failure"
TIMEOUT = "timeout"
TOOL_ERROR = "tool-error"
FAILURE_CATEGORIES = (
    PROSE_ONLY,
    NO_EDITS,
    REPEATED_COMMAND,
    VERIFIER_FAILURE,
    TIMEOUT,
    TOOL_ERROR,
)


def safe_model_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", model)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"malformed artifact: {path}")
    return value


def relative_path(path: Path, runs_dir: Path) -> str:
    try:
        return path.relative_to(runs_dir).as_posix()
    except ValueError as exc:
        raise ValueError(f"artifact is outside runs directory: {path}") from exc


def sample_log_path(summary_path: Path, sample: dict) -> Path:
    workdir = sample.get("workdir")
    if not isinstance(workdir, str) or not workdir:
        raise ValueError(f"malformed sample workdir: {summary_path}")
    relative_workdir = Path(workdir)
    if relative_workdir.is_absolute() or ".." in relative_workdir.parts:
        raise ValueError(f"unsafe sample workdir: {summary_path}")
    return summary_path.parent / relative_workdir / "harness-log.json"


def log_turns(log: dict, path: Path) -> list[dict]:
    turns = log.get("turns")
    if not isinstance(turns, list) or not all(isinstance(turn, dict) for turn in turns):
        raise ValueError(f"malformed artifact: {path}")
    return [turn for turn in turns if turn.get("kind") != "verification"]


def tool_calls(turns: list[dict]) -> list[dict]:
    calls = []
    for turn in turns:
        response = turn.get("response")
        if not isinstance(response, dict):
            continue
        value = response.get("tool_calls", [])
        if not isinstance(value, list):
            continue
        calls.extend(call for call in value if isinstance(call, dict))
    return calls


def call_name(call: dict) -> str:
    function = call.get("function")
    return function.get("name", "") if isinstance(function, dict) else ""


def call_arguments(call: dict) -> str:
    function = call.get("function")
    arguments = function.get("arguments", "") if isinstance(function, dict) else ""
    return arguments if isinstance(arguments, str) else json.dumps(arguments, sort_keys=True)


def classify_failure(sample: dict, log: dict) -> str:
    """Classify a recorded failure without changing the harness outcome.

    Categories are intentionally ordered from most specific observable cause to
    least specific: a repeated command is more useful than its eventual turn
    limit, and a prose-only completion is more useful than its verifier result.
    """
    if sample["passed"]:
        return PASS
    turns = log_turns(log, Path("<in-memory artifact>"))
    calls = tool_calls(turns)
    crash = sample.get("crashed") or log.get("crashed") or ""
    if not isinstance(crash, str):
        raise ValueError("malformed sample crash value")
    normalized_crash = crash.lower()

    if not calls:
        return PROSE_ONLY
    command_calls = [call_arguments(call) for call in calls if call_name(call) == "run_command"]
    if any(count > 1 for count in Counter(command_calls).values()):
        return REPEATED_COMMAND
    if not any(call_name(call) == "write_file" for call in calls):
        return NO_EDITS
    verification_failed = False
    for turn in log["turns"]:
        if not isinstance(turn, dict) or turn.get("kind") != "verification":
            continue
        result = turn.get("verify_result")
        if not isinstance(result, dict):
            raise ValueError("malformed verification artifact")
        if result.get("exit_code") != 0:
            verification_failed = True
    if "verify-command failed" in normalized_crash or verification_failed:
        return VERIFIER_FAILURE
    if any(marker in normalized_crash for marker in ("timed out", "timeout", "turn limit", "truncated")):
        return TIMEOUT
    return TOOL_ERROR


def read_case_artifact(runs_dir: Path, model: str, case_id: str, kind: str) -> dict:
    summary_path = runs_dir / f"{case_id}--{safe_model_name(model)}" / "harness-log-samples.json"
    if not summary_path.is_file():
        raise FileNotFoundError(f"missing artifact for {model}/{case_id}: {summary_path}")
    summary = read_json(summary_path)
    samples = summary.get("samples")
    passed_count, total = summary.get("passed_count"), summary.get("total")
    if not isinstance(samples, list) or not isinstance(passed_count, int) or not isinstance(total, int):
        raise ValueError(f"invalid harness sample summary: {summary_path}")
    if total != len(samples) or total < 1:
        raise ValueError(f"invalid harness sample summary: {summary_path}")
    if len({sample.get("sample") for sample in samples if isinstance(sample, dict)}) != len(samples):
        raise ValueError(f"invalid harness sample summary: {summary_path}")

    rows = []
    for sample in sorted(samples, key=lambda item: item.get("sample") if isinstance(item, dict) else -1):
        if not isinstance(sample, dict) or not isinstance(sample.get("sample"), int) or not isinstance(sample.get("passed"), bool):
            raise ValueError(f"invalid harness sample summary: {summary_path}")
        log_path = sample_log_path(summary_path, sample)
        if not log_path.is_file():
            raise FileNotFoundError(f"missing sample log: {log_path}")
        log = read_json(log_path)
        rows.append({
            "sample": sample["sample"],
            "passed": sample["passed"],
            "category": classify_failure(sample, log),
            "artifact": relative_path(log_path, runs_dir),
        })
    if passed_count != sum(sample["passed"] for sample in rows):
        raise ValueError(f"invalid harness sample summary: {summary_path}")
    counts = Counter(sample["category"] for sample in rows)
    failure_artifacts = {
        category: [sample["artifact"] for sample in rows if sample["category"] == category]
        for category in FAILURE_CATEGORIES
    }
    return {
        "case": case_id,
        "kind": kind,
        "samples": len(rows),
        "passed": passed_count,
        "failure_categories": {category: counts[category] for category in FAILURE_CATEGORIES},
        "failure_artifacts": failure_artifacts,
        "artifact": relative_path(summary_path, runs_dir),
        "sample_artifacts": rows,
    }


def summarize(runs_dir: Path, models: list[str]) -> list[dict]:
    runs_dir = runs_dir.resolve()
    rows = []
    for model in models:
        cases = [read_case_artifact(runs_dir, model, case_id, case["kind"]) for case_id, case in CASES.items()]
        grouped = defaultdict(lambda: {"passed": 0, "total": 0, "categories": Counter()})
        for case in cases:
            for name in ("overall", case["kind"]):
                grouped[name]["passed"] += case["passed"]
                grouped[name]["total"] += case["samples"]
                grouped[name]["categories"].update(case["failure_categories"])
        slices = {
            name: {
                "passed": values["passed"],
                "total": values["total"],
                "pass_rate": values["passed"] / values["total"],
                "failure_categories": {category: values["categories"][category] for category in FAILURE_CATEGORIES},
            }
            for name, values in grouped.items()
        }
        rows.append({"model": model, "cases": cases, "slices": slices})
    return rows


def fixture_revision() -> str | None:
    """Return the committed fixture tree ID when this checkout has one."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD:evals/python-coder/fixtures"], cwd=ROOT.parent.parent,
            check=True, text=True, capture_output=True,
        ).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def comparison_manifest(runs_dir: Path, rows: list[dict], harness_flags: list[str], revision: str | None = None) -> dict:
    return {
        "format": "python-coder-comparison-v1",
        "models": [row["model"] for row in rows],
        "samples": {
            row["model"]: {case["case"]: case["samples"] for case in row["cases"]}
            for row in rows
        },
        "harness_flags": harness_flags,
        "fixture_revision": revision if revision is not None else fixture_revision(),
        "output_root": str(runs_dir.resolve()),
    }


def render_markdown(rows: list[dict]) -> str:
    lines = ["# Python Coder evaluation summary", "", "| Model | Overall | Isolated | Repo-context |", "| --- | --- | --- | --- |"]
    for row in rows:
        slices = row["slices"]
        def cell(name: str) -> str:
            slice_ = slices[name]
            return f"{slice_['passed']}/{slice_['total']} ({slice_['pass_rate']:.0%})"
        lines.append(f"| {row['model']} | {cell('overall')} | {cell('isolated')} | {cell('repo-context')} |")
    lines += ["", "## Per-case diagnostics", "", "| Model | Case | Kind | Samples | Failure categories | Artifact |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        for case in row["cases"]:
            categories = "; ".join(
                f"{name}: {count} ({', '.join(f'`{path}`' for path in case['failure_artifacts'][name])})"
                for name, count in case["failure_categories"].items() if count
            )
            lines.append(f"| {row['model']} | {case['case']} | {case['kind']} | {case['passed']}/{case['samples']} | {categories or 'none'} | `{case['artifact']}` |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs")
    parser.add_argument("--model", action="append", required=True, help="Model ID; repeat for comparisons")
    parser.add_argument("--harness-flag", action="append", default=[], help="Exact harness flag used for this comparison; repeat as needed")
    parser.add_argument("--fixture-revision", help="Fixture commit or tree ID, when known")
    parser.add_argument("--output", type=Path, help="Optional JSON report output path")
    parser.add_argument("--manifest", type=Path, help="Optional standalone comparison manifest output path")
    args = parser.parse_args()
    rows = summarize(args.runs_dir, args.model)
    manifest = comparison_manifest(args.runs_dir, rows, args.harness_flag, args.fixture_revision)
    report = {"manifest": manifest, "models": rows}
    print(render_markdown(rows), end="")
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.manifest:
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
