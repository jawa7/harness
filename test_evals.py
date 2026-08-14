#!/usr/bin/env python3
"""Fast self-checks for eval discovery, deterministic grading, and judge parsing."""
import json
import tempfile
from pathlib import Path

import evals
import qwen_agent


def demo():
    cases = evals.load_cases()
    assert {case["id"] for case in cases} >= {
        "url-summary", "page-cache", "czech-sports-10", "topic-research", "partial-after-error", "tool-json-recovery",
    }
    result = {
        "finished": True,
        "events": [
            {"tool": "web_search", "result": {"status": "ok", "results": [{"url": "https://one.test"}, {"url": "https://two.test"}]}},
            {"tool": "finish", "result": {"status": "ok"}},
        ],
        "messages": [{"role": "assistant", "content": "Sources: https://one.test and https://two.test"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        grade = evals.validate_case(
            {"checks": {"required_tools": ["web_search"], "min_unique_sources": 2, "min_output_urls": 2}},
            result, Path(tmp),
        )
    assert grade["passed"], grade
    # A fallback emitted after quota/turn recovery is not a model answer when
    # the final no-tool response was invalid, irrespective of recovery kind.
    with tempfile.TemporaryDirectory() as tmp:
        invalid_final_recovery = evals.validate_case(
            {"checks": {}},
            {"finished": True, "final_recovery_failed": True, "events": [], "messages": []},
            Path(tmp),
        )
    assert not invalid_final_recovery["passed"], invalid_final_recovery
    # Multi-source live cases require the requested number of fetches, not
    # merely one successful web_fetch followed by an invented comparison.
    with tempfile.TemporaryDirectory() as tmp:
        multi_grade = evals.validate_case(
            {"checks": {"min_tool_calls": {"web_fetch": 2}}},
            {"finished": True, "events": [{"tool": "web_fetch", "result": {"status": "ok"}}], "messages": []},
            Path(tmp),
        )
    assert not multi_grade["passed"] and "web_fetch" in multi_grade["failures"][0], multi_grade
    with tempfile.TemporaryDirectory() as tmp:
        article_grade = evals.validate_case(
            {"checks": {"forbidden_output_url_substrings": ["newsnow.com"]}},
            {"finished": True, "events": [], "messages": [
                {"role": "assistant", "content": "https://www.newsnow.com/sport"},
            ]}, Path(tmp),
        )
    assert not article_grade["passed"] and "forbidden URL" in article_grade["failures"][0], article_grade
    assert evals.render_case_task(
        {"task": "Compare {first_url} and {second_url}"},
        target_urls={"first_url": "https://one.test", "second_url": "https://two.test"},
    ) == "Compare https://one.test and https://two.test"
    judge = evals.parse_judge_response({"choices": [{"message": {"content": json.dumps({
        "relevance": 8, "completeness": 7, "sources": 7, "limitations": 9, "rationale": "good",
    })}}]})
    assert judge["passed"] and judge["average"] == 7.75, judge
    fenced_judge = evals.parse_judge_response({"choices": [{"message": {"content": "Here is the verdict:\n```json\n" + json.dumps({
        "relevance": 8, "completeness": 7, "sources": 7, "limitations": 9,
    }) + "\n```"}}]})
    assert fenced_judge["passed"], fenced_judge
    # Summary tasks do not require a source list or limitations section. Those
    # dimensions are deliberately absent from the judge's pass/fail score.
    summary_judge = evals.parse_judge_response({"choices": [{"message": {"content": json.dumps({
        "relevance": 8, "completeness": 7,
    })}}]}, applicable_dimensions=["relevance", "completeness"])
    assert summary_judge["passed"] and summary_judge["average"] == 7.5, summary_judge
    summary_prompt = evals.judge_prompt(
        {"task": "summarize", "judge_dimensions": ["relevance", "completeness"]}, result,
        {"passed": True}, {},
    )
    assert "sources" not in summary_prompt[-1]["content"], summary_prompt

    # Fixture-backed scenarios are offline-only; live scenarios must be
    # explicitly declared so fake example.test URLs cannot become model fails.
    offline_cases = evals.load_cases(mode="offline")
    live_cases = evals.load_cases(mode="live")
    assert all("offline" in case["modes"] for case in offline_cases)
    assert all("live" in case["modes"] for case in live_cases)
    assert {case["id"] for case in offline_cases}.isdisjoint({case["id"] for case in live_cases})
    assert {"live-multi-url-compare", "live-article-research-3"} <= {case["id"] for case in live_cases}

    # An unreachable live source is infrastructure evidence, not a failure of
    # the model's reasoning. It is retried by the CLI and then reported as
    # inconclusive.
    unavailable = {
        "finished": True,
        "events": [{"tool": "web_fetch", "result": {"status": "error", "message": "URLError: timed out"}}],
        "messages": [],
    }
    assert evals.classify_outcome(unavailable) == "inconclusive"
    partial_with_source = {
        "finished": True,
        "events": [
            {"tool": "web_search", "result": {"status": "ok", "results": [{"url": "https://source.test"}]}},
            {"tool": "web_fetch", "result": {"status": "error", "message": "HTTP 503"}},
        ],
        "messages": [],
    }
    assert evals.classify_outcome(partial_with_source) == "completed"

    # The judge gets only sources cited by the answer, while the complete cache
    # remains in the run artifact for auditing.
    evidence_result = {
        "finished": True,
        "events": [{"tool": "web_search", "result": {"status": "ok", "results": [
            {"title": "Cited", "url": "https://one.test", "snippet": "supporting excerpt"},
            {"title": "Unused", "url": "https://two.test", "snippet": "other excerpt"},
        ]}}],
        "messages": [{"role": "assistant", "content": "Claim: https://one.test"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        evidence = evals.build_evidence_pack(evidence_result, Path(tmp))
    assert [source["url"] for source in evidence["cited_sources"]] == ["https://one.test"], evidence
    prompt = evals.judge_prompt({"task": "test"}, evidence_result, {"passed": True}, evidence)
    assert "supporting excerpt" in prompt[-1]["content"]

    assert evals.aggregate_case_runs([True, True, False])["status"] == "pass"
    assert evals.aggregate_case_runs([True])["status"] == "diagnostic"

    # Offline mode intercepts web data but invokes the normal agent loop and
    # writes a debuggable result artifact.
    case = next(case for case in cases if case["id"] == "url-summary")
    calls = iter([
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{
            "id": "fetch", "function": {"name": "web_fetch", "arguments": json.dumps({"url": "https://docs.example.test/model-card"})},
        }]}}]},
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{
            "id": "finish", "function": {"name": "finish", "arguments": json.dumps({"summary": "A compact model."})},
        }]}}]},
    ])
    original_call_model = qwen_agent.call_model
    qwen_agent.call_model = lambda *_: next(calls)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = evals.run_case(case, "fake-model", "http://unused", "offline", Path(tmp) / "case")
            assert artifact["deterministic"]["passed"], artifact
            assert (Path(tmp) / "case" / "result.json").is_file()
    finally:
        qwen_agent.call_model = original_call_model
    print("all checks passed")


if __name__ == "__main__":
    demo()
