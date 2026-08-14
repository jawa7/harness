#!/usr/bin/env python3
"""Run repeatable offline or live qwen-harness agent evals against local models."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import evals
import qwen_agent


def _judge(case: dict, artifact: dict, args) -> dict:
    """Judge completed runs only; infrastructure failures are not model answers."""
    try:
        judge_messages = evals.judge_prompt(
            case, artifact["result"], artifact["deterministic"], artifact["evidence"],
        )
        body = qwen_agent.call_model(
            args.judge_base_url or args.base_url, args.judge_model,
            judge_messages,
            0, 1000, [],
        )
        try:
            return evals.parse_judge_response(body, case.get("judge_dimensions"))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
            malformed = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            repair_messages = [
                {
                    "role": "system",
                    "content": (
                        "The previous evaluator response was malformed. Re-evaluate the original task, answer, "
                        "and evidence below; do not infer scores from the malformed response. Return JSON only, "
                        "with integer 0-10 scores for: "
                        f"{', '.join(case.get('judge_dimensions', ['relevance', 'completeness', 'sources', 'limitations']))}, "
                        "plus rationale."
                    ),
                },
                *judge_messages,
                {"role": "assistant", "content": malformed},
                {"role": "user", "content": "Return the corrected evaluation as one JSON object only."},
            ]
            repaired_body = qwen_agent.call_model(
                args.judge_base_url or args.base_url, args.judge_model, repair_messages, 0, 500, [],
            )
            return {**evals.parse_judge_response(repaired_body, case.get("judge_dimensions")), "repaired": True}
    except Exception as exc:
        return {
            "passed": False, "inconclusive": True,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _target_candidates(case: dict, live_targets: dict) -> list[dict[str, str]]:
    """Build primary/backup placeholder maps from a case's allowlisted targets."""
    keys = case.get("target_keys")
    if keys is None and case.get("target_key"):
        keys = {"target_url": case["target_key"]}
    if not keys:
        return [{}]
    values_by_placeholder = {}
    for placeholder, target_key in keys.items():
        values = live_targets.get(target_key, [])
        if not values:
            raise ValueError(f"live case {case['id']!r} has no configured target {target_key!r}")
        values_by_placeholder[placeholder] = values
    attempts = max(len(values) for values in values_by_placeholder.values())
    return [
        {placeholder: values[min(index, len(values) - 1)] for placeholder, values in values_by_placeholder.items()}
        for index in range(attempts)
    ]


def _write_report(root: Path, rows: list[dict]) -> None:
    overlap = any(row.get("judge_overlaps_target", False) for row in rows)
    report = [
        "# Eval report", "",
        "| Model | Case | Successful runs | Normalized calls | Final recovery | Inconclusive | Overall |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        aggregate = row["aggregate"]
        successful_runs = (
            f"{aggregate['successes']}/{row['completed_runs']}"
            if aggregate["successes"] is not None else "pending judge"
        )
        report.append(
            f"| {row['model']} | {row['case']} | {successful_runs} | "
            f"{row['normalized_content_tool_calls']} | "
            f"loop {row['loop_recoveries']} ({row['failed_loop_recoveries']} invalid); "
            f"quota {row['web_limit_recoveries']}; turns {row['turn_limit_recoveries']} | "
            f"{row['inconclusive_runs']} | {row['status']} |"
        )
    if overlap:
        report.extend(["", "> Warning: judge overlaps target model; scores may be self-evaluated."])
    (root / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    (root / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def _recalculate_row(row: dict) -> None:
    """Derive one result per repetition from its final retry and judge result."""
    final_attempts = {}
    for attempt in row["attempts"]:
        final_attempts[attempt["repetition"]] = attempt
    completed_passes = []
    inconclusive_runs = 0
    for attempt in final_attempts.values():
        if attempt["outcome"] == "inconclusive" or attempt["judge"].get("inconclusive"):
            inconclusive_runs += 1
        else:
            completed_passes.append(
                attempt["deterministic"]["passed"] and attempt["judge"].get("passed", False)
            )
    aggregate = evals.aggregate_case_runs(completed_passes)
    if inconclusive_runs:
        aggregate = {**aggregate, "status": "inconclusive", "passed": None}
    row.update({
        "status": aggregate["status"], "aggregate": aggregate,
        "completed_runs": len(completed_passes), "inconclusive_runs": inconclusive_runs,
    })


def _judge_saved_run(args) -> Path:
    """Score a prior collection run without calling the target model again."""
    root = Path(args.judge_run)
    summary_path = root / "summary.json"
    if not summary_path.is_file():
        raise ValueError(f"no saved eval summary at {summary_path}")
    rows = json.loads(summary_path.read_text(encoding="utf-8"))
    fallback_cases = {case["id"]: case for case in evals.load_cases(mode=args.mode)}
    for row in rows:
        row["judge_overlaps_target"] = args.judge_model == row["model"]
        for attempt in row["attempts"]:
            attempt_dir = (
                root / row["model"].replace("/", "_") / row["case"] /
                f"run-{attempt['repetition']:02d}" / f"attempt-{attempt['retry']:02d}"
            )
            result_path = attempt_dir / "result.json"
            if not result_path.is_file():
                raise ValueError(f"saved attempt is missing {result_path}")
            artifact = json.loads(result_path.read_text(encoding="utf-8"))
            case = artifact.get("case_definition") or fallback_cases.get(row["case"])
            if case is None:
                raise ValueError(
                    f"saved attempt has no frozen case definition and case {row['case']!r} is unavailable"
                )
            judge = {"skipped": True, "reason": "inconclusive infrastructure"}
            if artifact["outcome"] != "inconclusive":
                judge = _judge(case, artifact, args)
            attempt["judge"] = judge
            (attempt_dir / "judge.json").write_text(json.dumps(judge, indent=2, ensure_ascii=False), encoding="utf-8")
        _recalculate_row(row)
    _write_report(root, rows)
    return root


def run(args) -> Path:
    """Collect, judge, or fully run evals; return the persistent report directory."""
    phase = getattr(args, "phase", "all")
    if phase == "judge":
        return _judge_saved_run(args)
    cases = evals.load_cases(mode=args.mode)
    if args.case_ids:
        wanted = set(args.case_ids)
        cases = [case for case in cases if case["id"] in wanted]
    if not cases:
        raise ValueError(f"no {args.mode} eval cases matched")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(args.runs_dir) / timestamp
    live_targets = evals.load_live_targets() if args.mode == "live" else {}
    rows = []
    overlap = args.judge_model in set(args.model) if args.judge_model else False

    for model in args.model:
        for case in cases:
            completed_passes = []
            attempts = []
            inconclusive_runs = 0
            loop_recoveries = 0
            failed_loop_recoveries = 0
            web_limit_recoveries = 0
            turn_limit_recoveries = 0
            normalized_calls = 0
            candidates = _target_candidates(case, live_targets)
            for repetition in range(1, args.repetitions + 1):
                final_artifact = None
                final_judge = None
                # One retry is reserved for unavailable infrastructure. If the
                # case has a backup target, that retry switches to it.
                for retry in range(2):
                    target_urls = candidates[retry % len(candidates)]
                    target_url = target_urls.get("target_url")
                    attempt_dir = root / model.replace("/", "_") / case["id"] / f"run-{repetition:02d}" / f"attempt-{retry + 1:02d}"
                    artifact = evals.run_case(
                        case, model, args.base_url, args.mode, attempt_dir, target_url, target_urls,
                    )
                    judge = {"skipped": True, "reason": "pending separate judge"}
                    if phase == "all" and artifact["outcome"] != "inconclusive":
                        judge = _judge(case, artifact, args)
                    (attempt_dir / "judge.json").write_text(
                        json.dumps(judge, indent=2, ensure_ascii=False), encoding="utf-8",
                    )
                    attempts.append({
                        "repetition": repetition, "retry": retry + 1, "target_url": target_url,
                        "target_urls": target_urls,
                        "outcome": artifact["outcome"], "deterministic": artifact["deterministic"], "judge": judge,
                        "recovered_from_loop": artifact["result"].get("recovered_from_loop", False),
                        "loop_recovery_failed": artifact["result"].get("loop_recovery_failed", False),
                        "recovered_from_web_limit": artifact["result"].get("recovered_from_web_limit", False),
                        "recovered_from_turn_limit": artifact["result"].get("recovered_from_turn_limit", False),
                    })
                    loop_recoveries += int(artifact["result"].get("recovered_from_loop", False))
                    failed_loop_recoveries += int(artifact["result"].get("loop_recovery_failed", False))
                    web_limit_recoveries += int(artifact["result"].get("recovered_from_web_limit", False))
                    turn_limit_recoveries += int(artifact["result"].get("recovered_from_turn_limit", False))
                    normalized_calls += artifact["result"].get("normalized_content_tool_calls", 0)
                    final_artifact, final_judge = artifact, judge
                    if artifact["outcome"] != "inconclusive" or retry == 1:
                        break
                if phase == "collect":
                    continue
                if final_artifact["outcome"] == "inconclusive" or final_judge.get("inconclusive"):
                    inconclusive_runs += 1
                else:
                    completed_passes.append(
                        final_artifact["deterministic"]["passed"] and final_judge.get("passed", False)
                    )
            if phase == "collect":
                aggregate = {"status": "pending_judge", "passed": None, "successes": None, "runs": args.repetitions}
            else:
                aggregate = evals.aggregate_case_runs(completed_passes)
                if inconclusive_runs:
                    aggregate = {**aggregate, "status": "inconclusive", "passed": None}
            rows.append({
                "model": model, "case": case["id"], "status": aggregate["status"],
                "aggregate": aggregate, "completed_runs": len(completed_passes),
                "inconclusive_runs": inconclusive_runs, "repetitions": args.repetitions,
                "loop_recoveries": loop_recoveries, "failed_loop_recoveries": failed_loop_recoveries,
                "web_limit_recoveries": web_limit_recoveries, "turn_limit_recoveries": turn_limit_recoveries,
                "normalized_content_tool_calls": normalized_calls,
                "judge_overlaps_target": overlap, "attempts": attempts,
            })

    root.mkdir(parents=True, exist_ok=True)
    _write_report(root, rows)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("offline", "live"))
    parser.add_argument("--model", action="append", help="Model under test; repeat for comparisons")
    parser.add_argument("--base-url", default="http://localhost:1234/v1")
    parser.add_argument("--judge-model", help="OpenAI-compatible model used as the LLM judge")
    parser.add_argument("--judge-base-url", help="Judge endpoint; defaults to --base-url")
    parser.add_argument("--case", action="append", dest="case_ids", help="Case ID to run; repeat to filter")
    parser.add_argument("--runs-dir", default="evals/runs")
    parser.add_argument("--repetitions", type=int, default=3, help="Runs per model/case (default: 3; one run is diagnostic)")
    parser.add_argument("--phase", choices=("all", "collect", "judge"), default="all", help="all runs both models; collect saves target artifacts; judge scores a saved collection")
    parser.add_argument("--judge-run", help="Existing run directory to score in --phase judge")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    if args.phase in {"all", "collect"} and not args.model:
        parser.error("--model is required unless --phase judge")
    if args.phase in {"all", "judge"} and not args.judge_model:
        parser.error("--judge-model is required unless --phase collect")
    if args.phase == "judge" and not args.judge_run:
        parser.error("--judge-run is required with --phase judge")
    try:
        root = run(args)
    except ValueError as exc:
        parser.error(str(exc))
    print(root / "report.md")
    rows = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    if any(row["status"] == "fail" for row in rows):
        sys.exit(1)
    if any(row["status"] == "inconclusive" for row in rows):
        sys.exit(2)


if __name__ == "__main__":
    main()
