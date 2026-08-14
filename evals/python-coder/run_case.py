#!/usr/bin/env python3
"""Seed one Python Coder fixture into native harness sample workdirs and run it."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent.parent
CASES = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))


def output_path(case_id: str, model: str, requested: str | None) -> Path:
    if requested:
        return Path(requested).resolve()
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)
    return (ROOT / "runs" / f"{case_id}--{safe_model}").resolve()


def seed_samples(case_id: str, destination: Path, samples: int) -> None:
    starter = ROOT / "fixtures" / case_id / "starter"
    if not starter.is_dir():
        starter = ROOT / "fixtures" / "pyfixture-tasktracker" / "base"
        overlay = ROOT / "fixtures" / case_id / "overlay"
        if not starter.is_dir() or not overlay.is_dir():
            raise ValueError(f"case {case_id!r} has no starter directory or task-tracker overlay")
    else:
        overlay = None
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"refusing to overwrite existing run directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for sample in range(1, samples + 1):
        sample_dir = destination / "samples" / f"sample-{sample}"
        shutil.copytree(starter, sample_dir)
        if overlay:
            shutil.copytree(overlay, sample_dir, dirs_exist_ok=True)


def command_for(
    case_id: str, model: str, samples: int, output: Path, base_url: str, recovery_turns: int = 0,
    enable_web_access: bool = False,
) -> list[str]:
    verify = CASES[case_id]["verify"].format(suite_root=ROOT, python=sys.executable)
    command = [
        sys.executable, str(REPO_ROOT / "qwen_agent.py"),
        "--workdir", str(output), "--system", str(ROOT / "system.txt"),
        "--task", str(ROOT / "fixtures" / case_id / "task.txt"),
        "--model", model, "--base-url", base_url, "--max-turns", "16",
        "--enable-run-command", "--allowed-commands", f"pytest,python3,rg,{sys.executable}",
        "--verify-command", verify, "--samples", str(samples),
        # Repo-context coding runs opt in to the harness's metadata-only
        # preflight: local models otherwise repeatedly assume every test suite
        # lives under tests/, even when this fixture deliberately puts it at root.
        "--bootstrap-workspace-context", "--bootstrap-include-verify-command",
    ]
    if recovery_turns:
        command.extend(["--recovery-turns", str(recovery_turns)])
    # ponytail: off by default -- these fixtures are synthetic and stdlib-only, so
    # there's nothing for the model to look up; opt in only when testing against a
    # task that actually needs outside documentation. Requires TAVILY_API_KEY.
    if enable_web_access:
        command.append("--enable-web-access")
    return command


def harness_environment() -> dict[str, str]:
    """Make the active venv's test executables available to agent self-checks."""
    environment = os.environ.copy()
    executable_dir = str(Path(sys.executable).parent)
    environment["PATH"] = executable_dir + os.pathsep + environment.get("PATH", "")
    return environment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", nargs="?", choices=sorted(CASES))
    parser.add_argument("--list", action="store_true", help="Print case IDs, one per line")
    parser.add_argument("--model")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--recovery-turns", type=int, default=0, help="Opt in to bounded post-verification repair turns for each harness sample")
    parser.add_argument("--base-url", default="http://localhost:1234/v1")
    parser.add_argument("--output-dir")
    parser.add_argument("--enable-web-access", action="store_true", help="Give the model web_search/web_fetch alongside run_command. Requires TAVILY_API_KEY.")
    args = parser.parse_args()
    if args.list:
        print("\n".join(CASES))
        return
    if not args.case or not args.model:
        parser.error("case and --model are required unless --list is used")
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    if args.recovery_turns < 0:
        parser.error("--recovery-turns must be zero or greater")
    try:
        __import__("pytest")
    except ModuleNotFoundError:
        parser.error("pytest is required; install evals/python-coder/requirements.txt first")
    output = output_path(args.case, args.model, args.output_dir)
    try:
        seed_samples(args.case, output, args.samples)
    except ValueError as exc:
        parser.error(str(exc))
    raise SystemExit(subprocess.run(
        command_for(
            args.case, args.model, args.samples, output, args.base_url, args.recovery_turns,
            enable_web_access=args.enable_web_access,
        ),
        env=harness_environment(),
    ).returncode)


if __name__ == "__main__":
    main()
