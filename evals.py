#!/usr/bin/env python3
"""Reusable runner helpers for qwen-harness agent evals.

Cases live in ``evals/cases/*.json``. Offline cases replace only web tool
responses with declared fixtures; model calls still go to the model selected by
the command line. This keeps source facts deterministic without pretending the
agent behaviour itself is mocked.
"""
import json
import re
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import qwen_agent


ROOT = Path(__file__).parent
CASES_DIR = ROOT / "evals" / "cases"
LIVE_TARGETS_PATH = ROOT / "evals" / "live_targets.json"
URL_RE = re.compile(r"https?://[^\s)>\]]+")
DATE_RE = re.compile(r"\b(?:20\d{2}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]20\d{2})\b")


def load_cases(cases_dir: Path = CASES_DIR, mode: str = None) -> list[dict]:
    cases = []
    for path in sorted(cases_dir.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        case["modes"] = case.get("modes", ["offline"])
        if mode and mode not in case["modes"]:
            continue
        case["_path"] = str(path)
        cases.append(case)
    return cases


def load_live_targets(path: Path = LIVE_TARGETS_PATH) -> dict[str, list[str]]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_case_task(
    case: dict, target_url: str = None, target_urls: dict[str, str] = None,
) -> str:
    """Render dynamic date and allowlisted target placeholders for one run."""
    values = {"today": datetime.now(timezone.utc).date().isoformat(), "target_url": target_url or ""}
    values.update(target_urls or {})
    return case["task"].format(**values)


def result_text(messages: list[dict]) -> str:
    """Collect user-visible assistant content plus finish summaries."""
    parts = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        if message.get("content"):
            parts.append(message["content"])
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            if function.get("name") == "finish":
                try:
                    summary = json.loads(function.get("arguments") or "{}").get("summary", "")
                except json.JSONDecodeError:
                    summary = ""
                if summary:
                    parts.append(summary)
    return "\n".join(parts)


def event_urls(events: list[dict]) -> set[str]:
    urls = set()
    for event in events:
        result = event.get("result", {})
        if result.get("url"):
            urls.add(result["url"])
        for item in result.get("results", []):
            if item.get("url"):
                urls.add(item["url"])
    return urls


def _output_urls(output: str) -> set[str]:
    return {url.rstrip(".,;:)") for url in URL_RE.findall(output)}


def build_evidence_pack(result: dict, cache_dir: Path, excerpt_chars: int = 1600) -> dict:
    """Save compact, auditable evidence and select cited sources for the judge."""
    output = result_text(result.get("messages", []))
    cited_urls = _output_urls(output)
    sources_by_url = {}
    for event in result.get("events", []):
        tool_result = event.get("result", {})
        if tool_result.get("status") != "ok":
            continue
        for item in tool_result.get("results", []):
            url = item.get("url")
            if url:
                sources_by_url[url] = {
                    "url": url, "title": item.get("title", ""),
                    "excerpt": item.get("snippet", "")[:excerpt_chars], "tool": event["tool"],
                }
        url = tool_result.get("url")
        if url:
            source = sources_by_url.setdefault(url, {"url": url, "tool": event["tool"]})
            source["fetch_id"] = tool_result.get("fetch_id")
            source["excerpt"] = tool_result.get("text", "")[:excerpt_chars]
            fetch_id = tool_result.get("fetch_id")
            if fetch_id:
                text_path = cache_dir / fetch_id / "text.txt"
                if text_path.is_file():
                    source["excerpt"] = text_path.read_text(encoding="utf-8")[:excerpt_chars]
                    source["cache_path"] = str(text_path)
    sources = list(sources_by_url.values())
    cited_sources = [source for source in sources if source["url"] in cited_urls]
    return {
        "cited_urls": sorted(cited_urls), "cited_sources": cited_sources,
        "available_sources": sources,
    }


def classify_outcome(result: dict) -> str:
    """Separate unavailable live infrastructure from an agent behaviour failure."""
    infrastructure_markers = (
        "urlerror", "timed out", "timeout", "nodename nor servname", "temporary failure",
        "connection refused", "connection reset", "rate limit", "http error 5", "http 5", "status 5",
    )
    web_errors = [
        str(event.get("result", {}).get("message", "")).lower()
        for event in result.get("events", [])
        if event.get("tool") in {"web_search", "web_fetch"} and event.get("result", {}).get("status") == "error"
    ]
    has_successful_source = bool(event_urls([
        event for event in result.get("events", []) if event.get("result", {}).get("status") == "ok"
    ]))
    # A later outage after a successful source is a genuine partial-answer
    # scenario. Only an outage before any usable source makes the run unable to
    # judge the model's web behaviour.
    if not has_successful_source and any(marker in message for message in web_errors for marker in infrastructure_markers):
        return "inconclusive"
    return "completed"


def aggregate_case_runs(passes: list[bool]) -> dict:
    """Apply the agreed 2/3 threshold; one run is diagnostic rather than a rating."""
    if len(passes) < 3:
        return {"status": "diagnostic", "passed": None, "successes": sum(passes), "runs": len(passes)}
    successes = sum(passes)
    return {"status": "pass" if successes >= 2 else "fail", "passed": successes >= 2, "successes": successes, "runs": len(passes)}


def validate_case(case: dict, result: dict, workdir: Path) -> dict:
    """Evaluate deterministic requirements without consulting an LLM judge."""
    rules = case.get("checks", {})
    events = result.get("events", [])
    output = result_text(result.get("messages", []))
    failures = []
    web_events = [
        event for event in events
        if event["tool"] in {"web_search", "web_fetch"}
        and event.get("network_request_executed", True)
    ]
    if not result.get("finished"):
        failures.append(f"agent did not finish: {result.get('crashed') or 'unknown reason'}")
    if result.get("final_recovery_failed"):
        failures.append("final recovery did not produce a user-facing final answer")
    elif result.get("loop_recovery_failed"):
        failures.append("loop recovery did not produce a user-facing final answer")
    if len(web_events) > qwen_agent.MAX_WEB_REQUESTS_PER_CHAT_TURN:
        failures.append(f"used {len(web_events)} web calls (limit {qwen_agent.MAX_WEB_REQUESTS_PER_CHAT_TURN})")
    required_tools = set(rules.get("required_tools", []))
    actual_tools = {event["tool"] for event in events}
    missing_tools = required_tools - actual_tools
    if missing_tools:
        failures.append(f"missing required tool calls: {', '.join(sorted(missing_tools))}")
    for tool_name, minimum in rules.get("min_tool_calls", {}).items():
        actual_count = sum(
            event["tool"] == tool_name
            and (tool_name not in {"web_search", "web_fetch"} or event.get("network_request_executed", True))
            for event in events
        )
        if actual_count < minimum:
            failures.append(f"called {tool_name} {actual_count} times; expected at least {minimum}")
    forbidden_tools = set(rules.get("forbidden_tools", [])) & actual_tools
    if forbidden_tools:
        failures.append(f"used forbidden tool calls: {', '.join(sorted(forbidden_tools))}")
    min_sources = rules.get("min_unique_sources", 0)
    source_urls = event_urls(events)
    if len(source_urls) < min_sources:
        failures.append(f"found {len(source_urls)} unique sources; expected at least {min_sources}")
    min_output_urls = rules.get("min_output_urls", 0)
    output_urls = _output_urls(output)
    if len(output_urls) < min_output_urls:
        failures.append(f"answer contains {len(output_urls)} URLs; expected at least {min_output_urls}")
    exact_output_urls = rules.get("exact_output_urls")
    if exact_output_urls is not None and len(output_urls) != exact_output_urls:
        failures.append(f"answer contains {len(output_urls)} URLs; expected exactly {exact_output_urls}")
    for forbidden in rules.get("forbidden_output_url_substrings", []):
        matching_urls = sorted(url for url in output_urls if forbidden.lower() in url.lower())
        if matching_urls:
            failures.append(f"answer contains forbidden URL substring {forbidden!r}: {matching_urls[0]}")
    min_output_dates = rules.get("min_output_dates", 0)
    output_dates = set(DATE_RE.findall(output))
    if len(output_dates) < min_output_dates:
        failures.append(f"answer contains {len(output_dates)} dates; expected at least {min_output_dates}")
    min_answer_chars = rules.get("min_answer_chars", 0)
    if len(output.strip()) < min_answer_chars:
        failures.append(f"answer has {len(output.strip())} characters; expected at least {min_answer_chars}")
    for expected_path in rules.get("expected_files", []):
        if not (workdir / expected_path).is_file():
            failures.append(f"expected file was not created: {expected_path}")
    for phrase in rules.get("required_phrases", []):
        if phrase.lower() not in output.lower():
            failures.append(f"answer is missing required phrase: {phrase!r}")
    return {
        "passed": not failures,
        "failures": failures,
        "web_requests": len(web_events),
        "unique_sources": len(source_urls),
        "output_urls": len(output_urls), "output_dates": len(output_dates),
    }


@contextmanager
def offline_web_fixtures(case: dict):
    """Install a case's deterministic web responses for one eval run."""
    fixtures = case.get("fixtures", {})
    original_search = qwen_agent.DISPATCH["web_search"]
    original_fetch = qwen_agent.tool_web_fetch
    successful_fetches = 0

    def fake_search(_workdir, args):
        query = args.get("query", "")
        response = fixtures.get("search", {}).get(query) or fixtures.get("search", {}).get("*")
        if response is None:
            return {"status": "error", "message": f"offline fixture has no search response for {query!r}"}
        return response

    def fake_fetch(_workdir, args, cache_dir=None):
        nonlocal successful_fetches
        injection = case.get("fault_injection", {}).get("web_fetch", {}) if isinstance(case.get("fault_injection"), dict) else {}
        if injection and successful_fetches >= injection.get("fail_after_successes", -1):
            return {"status": "error", "message": injection.get("message", "injected fetch failure")}
        url = args.get("url", "")
        response = fixtures.get("fetch", {}).get(url) or fixtures.get("fetch", {}).get("*")
        if response is None:
            return {"status": "error", "message": f"offline fixture has no fetch response for {url!r}"}
        response = dict(response)
        if response.get("status") == "ok" and cache_dir is not None:
            raw = response.pop("raw", response.get("text", "").encode("utf-8"))
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            full_text = response.pop("full_text", response.get("text", ""))
            metadata = qwen_agent.cache_fetched_page(
                cache_dir, url, raw, response.get("content_type", "text/plain; charset=utf-8"),
                full_text, response.get("cache_truncated", False),
            )
            response["fetch_id"] = metadata["fetch_id"]
            response["cached"] = True
        if response.get("status") == "ok":
            successful_fetches += 1
        return response

    qwen_agent.DISPATCH["web_search"] = fake_search
    qwen_agent.tool_web_fetch = fake_fetch
    try:
        yield
    finally:
        qwen_agent.DISPATCH["web_search"] = original_search
        qwen_agent.tool_web_fetch = original_fetch


def judge_prompt(case: dict, result: dict, deterministic: dict, evidence: dict = None) -> list[dict]:
    transcript = result.get("messages", [])
    trace = [{"tool": e["tool"], "status": e["result"].get("status")} for e in result.get("events", [])]
    dimensions = case.get("judge_dimensions", ["relevance", "completeness", "sources", "limitations"])
    all_rubric = {
        "relevance": "Does the answer address the task?",
        "completeness": "Does it provide enough of what the task requested?",
        "sources": "Are claims tied to appropriate sources/URLs?",
        "limitations": "Does it honestly state unavailable or uncertain material?",
    }
    content = {
        "task": case["task"],
        "answer": result_text(transcript),
        "tool_trace": trace,
        "deterministic_checks": deterministic,
        "cited_evidence": (evidence or {}).get("cited_sources", []),
        "rubric": {key: all_rubric[key] for key in dimensions},
    }
    return [
        {"role": "system", "content": (
            "You are a strict evaluator. Return JSON only with these integer 0-10 scores: "
            f"{', '.join(dimensions)}, plus rationale. Do not add unrequested criteria."
        )},
        {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
    ]


def parse_judge_response(body: dict, applicable_dimensions: list[str] = None) -> dict:
    content = body["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # Local models commonly wrap otherwise valid output in a Markdown fence
        # or a short lead-in. Decode the first object only; do not try to repair
        # arbitrary malformed JSON here.
        start = content.find("{")
        if start < 0:
            raise
        parsed, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(parsed, dict):
        raise TypeError("judge response must be a JSON object")
    dimensions = applicable_dimensions or ["relevance", "completeness", "sources", "limitations"]
    scores = {key: int(parsed[key]) for key in dimensions}
    if any(score < 0 or score > 10 for score in scores.values()):
        raise ValueError("judge score outside 0..10")
    average = sum(scores.values()) / len(scores)
    return {
        **scores, "dimensions": dimensions, "average": average,
        "rationale": parsed.get("rationale", ""), "passed": average >= 7 and min(scores.values()) >= 5,
    }


def run_case(
    case: dict, model: str, base_url: str, mode: str, run_dir: Path, target_url: str = None,
    target_urls: dict[str, str] = None,
) -> dict:
    """Run one case against one real model, saving its raw agent artifact."""
    run_dir.mkdir(parents=True, exist_ok=True)
    workdir = run_dir / "workdir"
    workdir.mkdir()
    selected_tools = case.get("tools", [])
    tools = qwen_agent.build_chat_tools(selected_tools, [])
    kwargs = dict(
        messages=[{"role": "user", "content": render_case_task(case, target_url, target_urls)}], system_prompt=qwen_agent.DEFAULT_CHAT_SYSTEM_PROMPT,
        workdir=workdir, tools=tools, allowed_bins=[], model=model, base_url=base_url,
        temperature=case.get("temperature", 0), max_tokens=case.get("max_tokens", 8000),
        max_turns=case.get("max_turns", 12), command_timeout=30, context_messages=case.get("context_messages", 20),
        # Every eval captures successful fetches, even when page-cache tools are
        # unavailable to the model. This preserves the evidence used by live runs.
        fetch_cache_dir=run_dir / "fetches",
    )
    with ExitStack() as stack:
        if mode == "offline":
            stack.enter_context(offline_web_fixtures(case))
        if case.get("fault_injection") == "first_response_length":
            original_call_model = qwen_agent.call_model
            injected = False

            def first_response_length(*args):
                nonlocal injected
                if not injected:
                    injected = True
                    return {"choices": [{"finish_reason": "length", "message": {"content": "", "tool_calls": []}}]}
                return original_call_model(*args)

            qwen_agent.call_model = first_response_length
            stack.callback(lambda: setattr(qwen_agent, "call_model", original_call_model))
        result = qwen_agent.run_chat_turn(**kwargs)
    deterministic = validate_case(case, result, workdir)
    evidence = build_evidence_pack(result, run_dir / "fetches")
    (run_dir / "evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    artifact = {
        "case": case["id"], "case_definition": case, "model": model, "mode": mode, "target_url": target_url,
        "target_urls": target_urls,
        "run_at": datetime.now(timezone.utc).isoformat(), "result": result,
        "deterministic": deterministic, "outcome": classify_outcome(result), "evidence": evidence,
    }
    (run_dir / "result.json").write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifact
