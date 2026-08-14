#!/usr/bin/env python3
"""Self-check the public eval runner aggregation and reporting contract."""
from argparse import Namespace
import json
import tempfile
from pathlib import Path

import evals
import qwen_agent
import run_evals


def demo():
    # The runner gives the judge one repair attempt when a local judge returns
    # prose instead of its required JSON envelope.
    original_call_model = qwen_agent.call_model
    judge_calls = [0]

    repair_prompts = []

    def repairable_judge(*args):
        judge_calls[0] += 1
        repair_prompts.append(args[2])
        content = "[]" if judge_calls[0] == 1 else json.dumps({
            "relevance": 8, "completeness": 8, "sources": 8, "limitations": 8,
        })
        return {"choices": [{"message": {"content": content}}]}

    qwen_agent.call_model = repairable_judge
    try:
        repaired = run_evals._judge(
            {"task": "test"}, {"result": {"messages": [], "events": []}, "deterministic": {"passed": True}, "evidence": {}},
            Namespace(judge_base_url=None, base_url="unused", judge_model="judge"),
        )
    finally:
        qwen_agent.call_model = original_call_model
    assert repaired["passed"] and repaired["repaired"] and judge_calls[0] == 2, repaired
    assert any('"task": "test"' in message["content"] for message in repair_prompts[1]), repair_prompts

    # Collection and judging are separate operations so a constrained local
    # server never needs the target and judge models resident at once.
    case_for_split = {"id": "split-case", "modes": ["offline"], "task": "summarize"}
    original_load_cases = evals.load_cases
    original_run_case = evals.run_case
    original_call_model = qwen_agent.call_model
    evals.load_cases = lambda mode=None: [case_for_split]

    def split_artifact(case, model, base_url, mode, run_dir, target_url=None, target_urls=None):
        artifact = {
            "case": case["id"], "model": model, "mode": mode, "outcome": "completed",
            "result": {"messages": [{"role": "assistant", "content": "saved answer"}], "events": [], "finished": True},
            "deterministic": {"passed": True}, "evidence": {"cited_sources": []},
        }
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(json.dumps(artifact))
        (run_dir / "evidence.json").write_text(json.dumps(artifact["evidence"]))
        return artifact

    evals.run_case = split_artifact
    judge_models = []
    qwen_agent.call_model = lambda _url, model, *_args: (
        judge_models.append(model) or {"choices": [{"message": {"content": json.dumps({
            "relevance": 8, "completeness": 8, "sources": 8, "limitations": 8,
        })}}]}
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            collected_root = run_evals.run(Namespace(
                mode="offline", model=["target-only"], base_url="unused", judge_model=None,
                judge_base_url=None, case_ids=None, runs_dir=tmp, repetitions=1, phase="collect", judge_run=None,
            ))
            collected = json.loads((Path(collected_root) / "summary.json").read_text())
            assert collected[0]["status"] == "pending_judge", collected
            assert judge_models == [], judge_models
            judged_root = run_evals.run(Namespace(
                mode="offline", model=None, base_url="unused", judge_model="separate-judge",
                judge_base_url=None, case_ids=None, runs_dir=tmp, repetitions=1, phase="judge", judge_run=str(collected_root),
            ))
            judged = json.loads((Path(judged_root) / "summary.json").read_text())
    finally:
        evals.load_cases = original_load_cases
        evals.run_case = original_run_case
        qwen_agent.call_model = original_call_model
    assert judged_root == collected_root and judged[0]["status"] == "diagnostic", judged
    assert judge_models == ["separate-judge"], judge_models

    # The later judge phase must use the case contract frozen with collection,
    # not whatever a case file happens to contain after the target run.
    frozen_case = {"id": "frozen", "task": "original task", "judge_dimensions": ["relevance"]}
    frozen_artifact = {
        "case": "frozen", "mode": "offline", "case_definition": frozen_case, "outcome": "completed",
        "result": {"messages": [{"role": "assistant", "content": "answer"}], "events": [], "finished": True},
        "deterministic": {"passed": True}, "evidence": {"cited_sources": []},
    }
    original_load_cases = evals.load_cases
    original_call_model = qwen_agent.call_model
    judge_prompts = []
    evals.load_cases = lambda mode=None: [{"id": "frozen", "task": "changed task"}]
    qwen_agent.call_model = lambda *args: (
        judge_prompts.append(args[2]) or {"choices": [{"message": {"content": json.dumps({"relevance": 8})}}]}
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "saved"
            attempt_dir = root / "target" / "frozen" / "run-01" / "attempt-01"
            attempt_dir.mkdir(parents=True)
            (attempt_dir / "result.json").write_text(json.dumps(frozen_artifact))
            (root / "summary.json").write_text(json.dumps([{
                "model": "target", "case": "frozen", "attempts": [{"repetition": 1, "retry": 1, "outcome": "completed", "deterministic": {"passed": True}}],
                "loop_recoveries": 0, "failed_loop_recoveries": 0, "web_limit_recoveries": 0, "turn_limit_recoveries": 0,
                "normalized_content_tool_calls": 0, "repetitions": 1,
            }]))
            run_evals.run(Namespace(mode="live", model=None, base_url="unused", judge_model="judge", judge_base_url=None,
                case_ids=None, runs_dir=tmp, repetitions=1, phase="judge", judge_run=str(root)))
    finally:
        evals.load_cases = original_load_cases
        qwen_agent.call_model = original_call_model
    assert any('"task": "original task"' in message["content"] for message in judge_prompts[0]), judge_prompts

    # A judge transport error leaves the saved target result inconclusive so
    # `--phase judge` can be retried without rerunning the target model.
    original_call_model = qwen_agent.call_model
    qwen_agent.call_model = lambda *_: (_ for _ in ()).throw(TimeoutError("judge unavailable"))
    try:
        judge_error = run_evals._judge(
            {"task": "test"}, {"result": {"messages": [], "events": []}, "deterministic": {}, "evidence": {}},
            Namespace(judge_base_url=None, base_url="unused", judge_model="judge"),
        )
    finally:
        qwen_agent.call_model = original_call_model
    assert judge_error["inconclusive"] is True, judge_error

    # The original all-in-one mode has the same judge-infrastructure outcome
    # as the split judge phase: it is not a target-model failure.
    original_load_cases = evals.load_cases
    original_run_case = evals.run_case
    original_call_model = qwen_agent.call_model
    evals.load_cases = lambda mode=None: [case_for_split]
    evals.run_case = split_artifact
    qwen_agent.call_model = lambda *_: (_ for _ in ()).throw(TimeoutError("judge unavailable"))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            all_root = run_evals.run(Namespace(
                mode="offline", model=["target-only"], base_url="unused", judge_model="unavailable-judge",
                judge_base_url=None, case_ids=None, runs_dir=tmp, repetitions=1, phase="all", judge_run=None,
            ))
            all_rows = json.loads((Path(all_root) / "summary.json").read_text())
    finally:
        evals.load_cases = original_load_cases
        evals.run_case = original_run_case
        qwen_agent.call_model = original_call_model
    assert all_rows[0]["status"] == "inconclusive", all_rows

    case = {
        "id": "case", "modes": ["live"], "task": "compare {first_url} and {second_url}",
        "target_keys": {"first_url": "first", "second_url": "second"},
    }
    original_load_cases = evals.load_cases
    original_targets = evals.load_live_targets
    original_run_case = evals.run_case
    original_call_model = qwen_agent.call_model
    evals.load_cases = lambda mode=None: [case]
    evals.load_live_targets = lambda: {
        "first": ["https://primary-one.test", "https://backup-one.test"],
        "second": ["https://primary-two.test", "https://backup-two.test"],
    }
    calls = []

    def fake_run_case(case, model, base_url, mode, run_dir, target_url=None, target_urls=None):
        calls.append(target_urls)
        # The first attempt is unavailable, so the runner must retry using the
        # backup target; each actual model run then passes.
        unavailable = run_dir.name == "attempt-01"
        artifact = {
            "case": case["id"], "model": model, "outcome": "inconclusive" if unavailable else "completed",
            "result": {"messages": [], "events": [], "recovered_from_loop": not unavailable,
                       "loop_recovery_failed": False, "normalized_content_tool_calls": 2}, "deterministic": {"passed": not unavailable},
            "evidence": {"cited_sources": []},
        }
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "result.json").write_text(json.dumps(artifact))
        return artifact

    evals.run_case = fake_run_case
    qwen_agent.call_model = lambda *_: {"choices": [{"message": {"content": json.dumps({
        "relevance": 8, "completeness": 8, "sources": 8, "limitations": 8,
    })}}]}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = run_evals.run(Namespace(
                mode="live", model=["judge-and-target"], base_url="unused",
                judge_model="judge-and-target", judge_base_url=None, case_ids=None,
                runs_dir=tmp, repetitions=3,
            ))
            report = (Path(root) / "report.md").read_text()
            summary = json.loads((Path(root) / "summary.json").read_text())
    finally:
        evals.load_cases = original_load_cases
        evals.load_live_targets = original_targets
        evals.run_case = original_run_case
        qwen_agent.call_model = original_call_model

    assert calls == [
        {"first_url": "https://primary-one.test", "second_url": "https://primary-two.test"},
        {"first_url": "https://backup-one.test", "second_url": "https://backup-two.test"},
    ] * 3, calls
    assert summary[0]["status"] == "pass", summary
    assert summary[0]["judge_overlaps_target"] is True, summary
    assert "judge overlaps target model" in report, report
    assert "Final recovery" in report, report
    assert "Normalized calls" in report, report
    print("all checks passed")


if __name__ == "__main__":
    demo()
