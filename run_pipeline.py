#!/usr/bin/env python3
"""Prototype orchestrator: template -> qwen_agent.py -> (optional) gemma_review.py -> retry.

Wayfinder ticket 02 (fame5-bible-v2-prototype/.scratch/qwen-harness-autonomy/):
chains the steps Claude currently runs by hand, so Claude reviews only the
final pipeline-result.json, not each step. Rough prototype, not hardened.

Usage:
    python3 run_pipeline.py --template templates/content-population.txt \\
        --vars vars.json --workdir /path/to/workdir \\
        --output-name output.fragment.html \\
        --verify-command "python3 /abs/path/check_structure.py output.fragment.html" \\
        [--visual-spec spec.txt] [--max-retries 3]

vars.json is a flat {placeholder: value} dict matching the template's $placeholders.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from string import Template

HARNESS_DIR = Path(__file__).parent


def fill_template(template_path: Path, variables: dict) -> str:
    text = template_path.read_text()
    return Template(text).substitute(variables)


def run_qwen_agent(workdir: Path, task_path: Path, args) -> dict:
    system_path = Path(args.system)
    cmd = [
        sys.executable, str(HARNESS_DIR / "qwen_agent.py"),
        "--workdir", str(workdir),
        "--system", str(system_path),
        "--task", str(task_path),
        "--log-prefix", "pipeline-run",
        "--enable-run-command",
        "--verify-command", args.verify_command,
    ]
    if args.allowed_commands:
        cmd += ["--allowed-commands", args.allowed_commands]
    subprocess.run(cmd, check=False)  # exit code isn't the source of truth; the log file is
    log_path = workdir / "pipeline-run.json"
    if not log_path.exists():
        return {"finished": False, "crashed": "no log file written"}
    return json.loads(log_path.read_text())


def run_gemma_review(workdir: Path, output_name: str, spec_path: str) -> dict:
    out_path = workdir / "pipeline-visual-review.json"
    cmd = [
        sys.executable, str(HARNESS_DIR / "gemma_review.py"),
        "--html", str(workdir / output_name),
        "--spec", spec_path,
        "--out", str(out_path),
    ]
    subprocess.run(cmd, check=False)
    if not out_path.exists():
        return {"pass": False, "notes": "gemma_review produced no output file"}
    return json.loads(out_path.read_text())


def run_pipeline(template_path, variables, workdir, output_name, run_agent_fn, run_visual_fn, visual_spec, max_retries):
    """Core retry loop, factored out of main() so it's testable with fake run_agent_fn/run_visual_fn
    (mirrors qwen_agent.py's run_once() extraction — never touches a live model in tests)."""
    task_path = workdir / "pipeline-task.txt"
    attempts = []
    for attempt in range(1, max_retries + 1):
        brief = fill_template(template_path, variables)
        if attempts:
            last = attempts[-1]
            brief += (
                f"\n\nATTEMPT {attempt} — the previous attempt failed with this "
                f"specific reason, fix exactly this: {last['reason']}"
            )
        task_path.write_text(brief)

        log = run_agent_fn(task_path)
        result = {"attempt": attempt, "finished": bool(log.get("finished")), "reason": None}

        if not log.get("finished"):
            result["reason"] = log.get("crashed") or "verify-command failed (see pipeline-run.md)"
            attempts.append(result)
            continue

        if visual_spec:
            visual = run_visual_fn()
            if not visual.get("pass"):
                result["finished"] = False
                result["reason"] = f"visual gate failed: {visual.get('notes', '')}"
                attempts.append(result)
                continue

        attempts.append(result)
        break

    passed = bool(attempts and attempts[-1]["finished"])
    return {
        "passed": passed,
        "attempts": len(attempts),
        "output_file": str(workdir / output_name) if passed else None,
        "history": attempts,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", required=True)
    ap.add_argument("--vars", required=True, help="Path to a JSON file of {placeholder: value}")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--system", required=True, help="Path to system prompt file")
    ap.add_argument("--output-name", required=True, help="Filename the model must write, e.g. output.fragment.html")
    ap.add_argument("--verify-command", required=True)
    ap.add_argument("--allowed-commands", default="")
    ap.add_argument("--visual-spec", default=None, help="If set, run gemma_review.py against this spec after verify passes")
    ap.add_argument("--max-retries", type=int, default=3)
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    variables = json.loads(Path(args.vars).read_text())

    summary = run_pipeline(
        template_path=Path(args.template),
        variables=variables,
        workdir=workdir,
        output_name=args.output_name,
        run_agent_fn=lambda task_path: run_qwen_agent(workdir, task_path, args),
        run_visual_fn=lambda: run_gemma_review(workdir, args.output_name, args.visual_spec),
        visual_spec=args.visual_spec,
        max_retries=args.max_retries,
    )
    (workdir / "pipeline-result.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    sys.exit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
