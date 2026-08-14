#!/usr/bin/env python3
"""Self-check for the run_command / verify / samples logic added to qwen_agent.py.

No LM Studio / model call involved — exercises execute_command() and run_once()
directly, with call_model monkeypatched to a canned response so this never touches
a live server (matters for --samples especially, since a real run would burn a
model call per sample).
Run: python3 test_qwen_agent.py
"""
import json
import io
import shutil
import tempfile
from pathlib import Path

import qwen_agent
from qwen_agent import (
    execute_command, derive_log_basename, run_once, TOOLS, _targets_verify_script,
    destructive_reason, resolve_pending_action, PENDING_ACTION_FILENAME,
    build_chat_tools, create_chat_session, load_chat_session, save_chat_session,
    trim_chat_messages, run_chat_turn, cache_fetched_page, save_fetched_page,
    print_fetched_page, resolve_cached_fetch, MAX_WEB_REQUESTS_PER_CHAT_TURN,
    workspace_inventory, build_workspace_bootstrap,
)


def _fake_call_model_factory(finish_ok=True):
    """Simulates the model immediately calling finish() on turn 1."""
    def fake(base_url, model, messages, temperature, max_tokens, tools):
        return {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "finish", "arguments": json.dumps({"summary": "done"})},
                    }],
                },
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    return fake


def _run_once_with_fake_model(workdir, verify_command):
    orig = qwen_agent.call_model
    qwen_agent.call_model = _fake_call_model_factory()
    try:
        return run_once(
            workdir=workdir, system_prompt="sys", task_prompt="task",
            tools=TOOLS, allowed_tool_names={t["function"]["name"] for t in TOOLS},
            allowed_bins=["python3"], model="fake-model", base_url="http://unused",
            temperature=0.0, max_tokens=100, max_turns=3,
            command_timeout=5,
            verify_command=verify_command, verify_timeout=5,
            log_basename="harness-log",
        )
    finally:
        qwen_agent.call_model = orig


def _tool_response(name, arguments, call_id="call_1"):
    return {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            },
        }],
    }


def _run_once_with_responses(workdir, responses, verify_command, recovery_turns=0, captured_messages=None, tools=TOOLS, **run_options):
    """Run a canned model transcript, including optional post-verification recovery."""
    remaining = iter(responses)
    original = qwen_agent.call_model

    def fake(*args):
        if captured_messages is not None:
            captured_messages.append(json.loads(json.dumps(args[2])))
        return next(remaining)

    qwen_agent.call_model = fake
    try:
        return run_once(
            workdir=workdir, system_prompt="sys", task_prompt="task",
            tools=tools, allowed_tool_names={t["function"]["name"] for t in tools},
            allowed_bins=["python3"], model="fake-model", base_url="http://unused",
            temperature=0.0, max_tokens=100, max_turns=3, command_timeout=5,
            verify_command=verify_command, verify_timeout=5, log_basename="harness-log",
            recovery_turns=recovery_turns, **run_options,
        )
    finally:
        qwen_agent.call_model = original


def demo():
    # Interactive chat: the tools the user did not select must not be sent to
    # the model. finish is internal to the chat loop and is always available.
    selected_tools = build_chat_tools(["read_file", "write_file"], [])
    assert {tool["function"]["name"] for tool in selected_tools} == {
        "read_file", "write_file", "finish",
    }
    try:
        build_chat_tools(["run_command", "web_search"], ["python3"])
        assert False, "web access and run_command must remain mutually exclusive"
    except ValueError:
        pass
    cache_tools = build_chat_tools(["web_fetch", "save_fetched_page", "print_fetched_page"], [])
    assert {tool["function"]["name"] for tool in cache_tools} >= {
        "web_fetch", "save_fetched_page", "print_fetched_page",
    }
    # Cache tools need a fetch source. This also protects resumed sessions that
    # were saved before setup began adding web_fetch automatically.
    cache_only_tools = build_chat_tools(["save_fetched_page", "print_fetched_page"], [])
    assert {tool["function"]["name"] for tool in cache_only_tools} >= {
        "web_fetch", "save_fetched_page", "print_fetched_page",
    }
    try:
        build_chat_tools(["run_command", "save_fetched_page"], ["python3"])
        assert False, "cache tools imply web_fetch and cannot combine with run_command"
    except ValueError:
        pass

    # A fetched page has an immutable on-disk snapshot. Saving raw preserves
    # its exact bytes; saving text and terminal printing use the full extracted
    # text rather than the short excerpt that is sent back to the model.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "fetches"
        raw = b"<html><body>\xc4\x8desk\xc3\xbd text</body></html>"
        fetch = cache_fetched_page(
            cache_dir, "https://example.test/page", raw, "text/html; charset=utf-8", "Czech\u00fd text", False,
        )
        latest = resolve_cached_fetch(cache_dir, "https://example.test/page")
        assert latest["fetch_id"] == fetch["fetch_id"]
        saved = save_fetched_page(
            root, cache_dir, {"url": "https://example.test/page", "path": "page", "format": "both"},
        )
        assert (root / "page.raw").read_bytes() == raw
        assert (root / "page.txt").read_text() == "Czech\u00fd text"
        output = io.StringIO()
        printed = print_fetched_page(cache_dir, {"url": "https://example.test/page", "format": "text"}, output)
        assert printed["chars_printed"] == len("Czech\u00fd text")
        assert output.getvalue() == "Czech\u00fd text\n"

    # Keep whole user turns when trimming context: a tool response without the
    # assistant tool-call that created it is invalid for OpenAI-compatible APIs.
    transcript = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ]
    assert trim_chat_messages(transcript, 2) == transcript[2:]
    long_turn = [
        {"role": "user", "content": "research"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "old_1"}]},
        {"role": "tool", "tool_call_id": "old_1", "content": "old result"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "new_1"}]},
        {"role": "tool", "tool_call_id": "new_1", "content": "new result"},
    ]
    assert trim_chat_messages(long_turn, 3) == [long_turn[0], *long_turn[-2:]]

    # Sessions persist configuration and the complete transcript separately
    # from the rolling context sent to the model.
    with tempfile.TemporaryDirectory() as tmp:
        sessions_dir = Path(tmp)
        session = create_chat_session(
            sessions_dir=sessions_dir, name="my first session", model="fake-model",
            base_url="http://localhost:1234/v1", workdir=sessions_dir / "work",
            system_prompt="You are helpful.", selected_tools=["read_file"],
            allowed_bins=[], max_turns=3, context_messages=2,
        )
        session["messages"].append({"role": "user", "content": "hello"})
        save_chat_session(session)
        loaded = load_chat_session(session["path"])
        assert loaded["config"]["model"] == "fake-model"
        assert loaded["messages"][-1]["content"] == "hello"

    # A chat turn reuses the existing tool dispatch but keeps its messages so
    # the next terminal prompt can continue the same conversation.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        calls = iter([
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "", "tool_calls": [{
                            "id": "write_1", "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "note.txt", "content": "saved"}),
                            },
                        }],
                    },
                }],
            },
            {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "", "tool_calls": [{
                            "id": "finish_1", "function": {
                                "name": "finish", "arguments": json.dumps({"summary": "done"}),
                            },
                        }],
                    },
                }],
            },
        ])
        original_call_model = qwen_agent.call_model
        qwen_agent.call_model = lambda *args: next(calls)
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "write a note"}], system_prompt="sys",
                workdir=workdir, tools=build_chat_tools(["write_file"], []),
                allowed_bins=[], model="fake-model", base_url="http://unused",
                temperature=0.0, max_tokens=100, max_turns=3, command_timeout=5,
                context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
        assert result["finished"] is True and result["summary"] == "done", result
        assert (workdir / "note.txt").read_text() == "saved"
        assert [message["role"] for message in result["messages"]] == [
            "user", "assistant", "tool", "assistant", "tool",
        ]

    # Three identical successful writes still need a user-facing final answer.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        write_calls = 0

        def repeat_write_model(*_args):
            nonlocal write_calls
            write_calls += 1
            if not _args[5]:
                return {"choices": [{"finish_reason": "stop", "message": {"content": "Wrote same.txt."}}]}
            return {
                "choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{
                    "id": f"write_{write_calls}", "function": {
                        "name": "write_file", "arguments": json.dumps({"path": "same.txt", "content": "same"}),
                    },
                }]}}],
            }

        original_call_model = qwen_agent.call_model
        qwen_agent.call_model = repeat_write_model
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "write"}], system_prompt="sys", workdir=workdir,
                tools=build_chat_tools(["write_file"], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=10, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
        assert result["finished"] and result["crashed"] is None and write_calls == 4, result
        assert result["recovered_from_loop"] is True and result["summary"] == "Wrote same.txt.", result

    # A malformed, length-truncated tool call gets exactly one recovery retry.
    with tempfile.TemporaryDirectory() as tmp:
        model_responses = iter([
            {"choices": [{"finish_reason": "length", "message": {"content": "", "tool_calls": []}}]},
            {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{
                "id": "finish", "function": {"name": "finish", "arguments": json.dumps({"summary": "recovered"})},
            }]}}]},
        ])
        original_call_model = qwen_agent.call_model
        qwen_agent.call_model = lambda *_: next(model_responses)
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "search"}], system_prompt="sys", workdir=Path(tmp),
                tools=build_chat_tools([], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=3, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
        assert result["finished"] and result["summary"] == "recovered", result

    # Some function-calling models (including xLAM) serialize an otherwise
    # valid batch of calls into assistant content rather than OpenAI's
    # ``tool_calls`` field. The harness accepts that exact, unambiguous shape
    # and routes it through the normal tool policy/dispatch path.
    with tempfile.TemporaryDirectory() as tmp:
        responses = iter([
            {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps([
                {"name": "web_fetch", "arguments": {"url": "https://example.test/model"}},
            ])}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "Fetched and summarized."}}]},
        ])
        original_call_model = qwen_agent.call_model
        original_web_fetch = qwen_agent.DISPATCH["web_fetch"]
        qwen_agent.call_model = lambda *_: next(responses)
        qwen_agent.DISPATCH["web_fetch"] = lambda *_: {"status": "ok", "url": "https://example.test/model", "text": "model card"}
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "read a model card"}], system_prompt="sys", workdir=Path(tmp),
                tools=build_chat_tools(["web_fetch"], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=3, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_fetch"] = original_web_fetch
        assert result["finished"] and result["summary"] == "Fetched and summarized.", result
        assert [event["tool"] for event in result["events"]] == ["web_fetch"], result
        assert result["normalized_content_tool_calls"] == 1, result

    # The fallback cannot activate arbitrary JSON prose: all names must be
    # tools enabled in the current session.
    with tempfile.TemporaryDirectory() as tmp:
        raw = json.dumps([{ "name": "web_fetch", "arguments": {"url": "https://example.test"}}])
        original_call_model = qwen_agent.call_model
        qwen_agent.call_model = lambda *_: {"choices": [{"finish_reason": "stop", "message": {"content": raw}}]}
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "show JSON"}], system_prompt="sys", workdir=Path(tmp),
                tools=build_chat_tools([], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=2, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
        assert result["events"] == [] and result["summary"] == raw, result

    # Regression: a tool-calling model can repeat the same successful fetch forever.
    # After the third identical call, chat makes one final model call with no tools,
    # carrying the collected material so the user gets an answer rather than a crash.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        call_count = 0
        prompt_lengths = []

        def repeated_failed_fetch(*args):
            nonlocal call_count
            call_count += 1
            prompt_lengths.append(len(args[2]))
            if not args[5]:
                return {
                    "choices": [{"finish_reason": "stop", "message": {
                        "content": "Summary from the fetched source.", "tool_calls": [],
                    }}],
                }
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "", "tool_calls": [{
                            "id": f"fetch_{call_count}", "function": {
                                "name": "web_fetch",
                                "arguments": json.dumps({"url": "https://example.test/missing"}),
                            },
                        }],
                    },
                }],
            }

        original_call_model = qwen_agent.call_model
        original_web_fetch = qwen_agent.DISPATCH["web_fetch"]
        qwen_agent.call_model = repeated_failed_fetch
        qwen_agent.DISPATCH["web_fetch"] = lambda *_: {"status": "ok", "text": "same result"}
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "summarize this"}], system_prompt="sys",
                workdir=workdir, tools=build_chat_tools(["web_fetch"], []),
                allowed_bins=[], model="fake-model", base_url="http://unused",
                temperature=0.0, max_tokens=100, max_turns=100, command_timeout=5,
                context_messages=3,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_fetch"] = original_web_fetch
        assert call_count == 4, call_count
        assert max(prompt_lengths) <= 4, prompt_lengths  # system + a 3-message rolling window
        assert result["finished"] is True and result["crashed"] is None, result
        assert result["summary"] == "Summary from the fetched source.", result
        assert result["recovered_from_loop"] is True, result

    # A recovery response that merely emits another serialized tool call is not
    # a user-facing answer. Preserve an honest fallback, but flag the run as a
    # failed loop recovery so evals do not count it as completed behaviour.
    with tempfile.TemporaryDirectory() as tmp:
        calls = [0]

        def markup_recovery_model(*args):
            calls[0] += 1
            if not args[5]:
                return {"choices": [{"finish_reason": "stop", "message": {
                    "content": '<tool_call>{"name":"web_search"}</tool_call>', "tool_calls": [],
                }}]}
            return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{
                "id": str(calls[0]), "function": {"name": "web_search", "arguments": json.dumps({"query": "same"})},
            }]}}]}

        original_call_model = qwen_agent.call_model
        original_web_search = qwen_agent.DISPATCH["web_search"]
        qwen_agent.call_model = markup_recovery_model
        qwen_agent.DISPATCH["web_search"] = lambda *_: {"status": "ok", "results": [{"url": "https://source.test"}]}
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "research"}], system_prompt="sys", workdir=Path(tmp),
                tools=build_chat_tools(["web_search"], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=10, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_search"] = original_web_search
        assert result["finished"] and result["recovered_from_loop"], result
        assert result["loop_recovery_failed"] is True, result
        assert "could not complete" in result["summary"], result

    # A user-provided URL must be fetched before search, and a successful fetch
    # of that URL ends web browsing: summarize that source instead of wandering
    # into unrelated search results. Denied searches never reach the handler.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        model_calls = iter([
            ("web_search", {"query": "unnecessary search"}),
            ("web_fetch", {"url": "https://example.test/page"}),
            ("web_search", {"query": "still unnecessary after fetch"}),
            ("finish", {"summary": "read the supplied page"}),
        ])
        handler_calls = {"search": 0, "fetch": 0}

        def url_policy_model(*_args):
            name, arguments = next(model_calls)
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [{
                        "id": name, "function": {"name": name, "arguments": json.dumps(arguments)},
                    }]},
                }],
            }

        original_call_model = qwen_agent.call_model
        original_web_search = qwen_agent.DISPATCH["web_search"]
        original_web_fetch = qwen_agent.DISPATCH["web_fetch"]
        qwen_agent.call_model = url_policy_model
        qwen_agent.DISPATCH["web_search"] = lambda *_: handler_calls.__setitem__("search", handler_calls["search"] + 1)
        qwen_agent.DISPATCH["web_fetch"] = lambda *_: (
            handler_calls.__setitem__("fetch", handler_calls["fetch"] + 1) or {"status": "ok", "text": "page"}
        )
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "Read https://example.test/page"}], system_prompt="sys",
                workdir=workdir, tools=build_chat_tools(["web_search", "web_fetch"], []),
                allowed_bins=[], model="fake-model", base_url="http://unused",
                temperature=0.0, max_tokens=100, max_turns=5, command_timeout=5,
                context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_search"] = original_web_search
            qwen_agent.DISPATCH["web_fetch"] = original_web_fetch
        assert result["finished"] is True, result
        assert handler_calls == {"search": 0, "fetch": 1}, handler_calls

    # If a later web operation fails after a source was obtained, browsing is
    # closed and the model is told to answer from the material it already has.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        model_calls = iter([
            ("web_search", {"query": "one useful source"}),
            ("web_fetch", {"url": "https://example.test/missing"}),
            ("web_search", {"query": "must be blocked after error"}),
            ("finish", {"summary": "partial answer from the search result"}),
        ])
        handler_calls = {"search": 0, "fetch": 0}

        def failure_after_source_model(*_args):
            name, arguments = next(model_calls)
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {"content": "", "tool_calls": [{
                        "id": name, "function": {"name": name, "arguments": json.dumps(arguments)},
                    }]},
                }],
            }

        original_call_model = qwen_agent.call_model
        original_web_search = qwen_agent.DISPATCH["web_search"]
        original_web_fetch = qwen_agent.DISPATCH["web_fetch"]
        qwen_agent.call_model = failure_after_source_model
        qwen_agent.DISPATCH["web_search"] = lambda *_: (
            handler_calls.__setitem__("search", handler_calls["search"] + 1) or {"status": "ok", "results": ["useful"]}
        )
        qwen_agent.DISPATCH["web_fetch"] = lambda *_: (
            handler_calls.__setitem__("fetch", handler_calls["fetch"] + 1) or {"status": "error", "message": "HTTP 404"}
        )
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "research without a supplied URL"}], system_prompt="sys",
                workdir=workdir, tools=build_chat_tools(["web_search", "web_fetch"], []),
                allowed_bins=[], model="fake-model", base_url="http://unused",
                temperature=0.0, max_tokens=100, max_turns=6, command_timeout=5,
                context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_search"] = original_web_search
            qwen_agent.DISPATCH["web_fetch"] = original_web_fetch
        assert result["finished"] is True, result
        assert handler_calls == {"search": 1, "fetch": 1}, handler_calls
        assert any(
            "existing successful web source" in message.get("content", "")
            for message in result["messages"] if message.get("role") == "tool"
        ), result["messages"]

    # Web browsing has a hard request quota independent of the broader turn
    # budget, so a model that keeps inventing different searches cannot spend
    # indefinitely. The 13th request is refused locally.
    with tempfile.TemporaryDirectory() as tmp:
        calls = iter([
            *[("web_search", {"query": f"query {index}"}) for index in range(MAX_WEB_REQUESTS_PER_CHAT_TURN + 1)],
            ("finish", {"summary": "enough sources"}),
        ])
        search_count = [0]

        def quota_model(*_args):
            name, arguments = next(calls)
            return {
                "choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{
                    "id": name, "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}}],
            }

        original_call_model = qwen_agent.call_model
        original_web_search = qwen_agent.DISPATCH["web_search"]
        qwen_agent.call_model = quota_model
        qwen_agent.DISPATCH["web_search"] = lambda *_: (
            search_count.__setitem__(0, search_count[0] + 1) or {"status": "ok", "results": [{"url": "https://example.test"}]}
        )
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "research"}], system_prompt="sys", workdir=Path(tmp),
                tools=build_chat_tools(["web_search"], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=20, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_search"] = original_web_search
        assert result["finished"], result
        search_results = [event for event in result["events"] if event["tool"] == "web_search"]
        assert len(search_results) == MAX_WEB_REQUESTS_PER_CHAT_TURN
        assert search_count[0] == MAX_WEB_REQUESTS_PER_CHAT_TURN
        assert result["recovered_from_web_limit"] is True, result
        assert result["recovered_from_loop"] is False and result["recovered_from_turn_limit"] is False, result

    # A single model response can contain a batch of web calls. Only the first
    # 12 may reach the network; the rest receive local refusals, then the
    # harness asks once for a final no-tool answer from gathered sources.
    with tempfile.TemporaryDirectory() as tmp:
        model_calls = [0]
        handler_calls = [0]

        def batch_quota_model(*args):
            model_calls[0] += 1
            if not args[5]:
                return {"choices": [{"finish_reason": "stop", "message": {"content": "Partial answer from sources."}}]}
            return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
                {"id": f"search-{index}", "function": {"name": "web_search", "arguments": json.dumps({"query": str(index)})}}
                for index in range(MAX_WEB_REQUESTS_PER_CHAT_TURN + 3)
            ]}}]}

        original_call_model = qwen_agent.call_model
        original_web_search = qwen_agent.DISPATCH["web_search"]
        qwen_agent.call_model = batch_quota_model
        qwen_agent.DISPATCH["web_search"] = lambda *_: (
            handler_calls.__setitem__(0, handler_calls[0] + 1) or {"status": "ok", "results": [{"url": "https://source.test"}]}
        )
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "research"}], system_prompt="sys", workdir=Path(tmp),
                tools=build_chat_tools(["web_search"], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=3, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_search"] = original_web_search
        assert handler_calls[0] == MAX_WEB_REQUESTS_PER_CHAT_TURN, result
        assert result["web_requests"] == MAX_WEB_REQUESTS_PER_CHAT_TURN, result
        assert result["finished"] and result["recovered_from_web_limit"], result
        assert result["summary"] == "Partial answer from sources.", result
        assert result["recovered_from_loop"] is False and result["recovered_from_turn_limit"] is False, result

    # Reaching the turn budget after useful work is a recovery condition, not
    # an empty crash: the model gets one no-tool response to produce a partial
    # answer from the evidence it already gathered.
    with tempfile.TemporaryDirectory() as tmp:
        def turn_limit_model(*args):
            if not args[5]:
                return {"choices": [{"finish_reason": "stop", "message": {"content": "Partial research answer."}}]}
            return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{
                "id": "search", "function": {"name": "web_search", "arguments": json.dumps({"query": "topic"})},
            }]}}]}

        original_call_model = qwen_agent.call_model
        original_web_search = qwen_agent.DISPATCH["web_search"]
        qwen_agent.call_model = turn_limit_model
        qwen_agent.DISPATCH["web_search"] = lambda *_: {"status": "ok", "results": [{"url": "https://source.test"}]}
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "research"}], system_prompt="sys", workdir=Path(tmp),
                tools=build_chat_tools(["web_search"], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=1, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_search"] = original_web_search
        assert result["finished"] and result["recovered_from_turn_limit"], result
        assert result["summary"] == "Partial research answer.", result
        assert result["recovered_from_loop"] is False and result["recovered_from_web_limit"] is False, result

    # A no-tool recovery must not accept a native or content-serialized tool
    # call as user-facing prose.
    with tempfile.TemporaryDirectory() as tmp:
        recovery_calls = [0]

        def invalid_recovery_model(*args):
            recovery_calls[0] += 1
            if not args[5]:
                return {"choices": [{"finish_reason": "tool_calls", "message": {
                    "content": '[{"name":"web_search","arguments":{"query":"again"}}]',
                    "tool_calls": [{"id": "again", "function": {"name": "web_search", "arguments": '{"query":"again"}'}}],
                }}]}
            return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [{
                "id": "search", "function": {"name": "web_search", "arguments": json.dumps({"query": "topic"})},
            }]}}]}

        original_call_model = qwen_agent.call_model
        original_web_search = qwen_agent.DISPATCH["web_search"]
        qwen_agent.call_model = invalid_recovery_model
        qwen_agent.DISPATCH["web_search"] = lambda *_: {"status": "ok", "results": [{"url": "https://source.test"}]}
        try:
            result = run_chat_turn(
                messages=[{"role": "user", "content": "research"}], system_prompt="sys", workdir=Path(tmp),
                tools=build_chat_tools(["web_search"], []), allowed_bins=[], model="fake", base_url="unused",
                temperature=0, max_tokens=100, max_turns=1, command_timeout=5, context_messages=20,
            )
        finally:
            qwen_agent.call_model = original_call_model
            qwen_agent.DISPATCH["web_search"] = original_web_search
        assert result["final_recovery_failed"] and result["recovered_from_turn_limit"], result
        assert "turn limit" in result["summary"], result

    assert qwen_agent.tool_web_search(Path("."), {"query": "x" * 301})["status"] == "error"

    # Default task.txt keeps the pre-existing filename (backward compatible)
    assert derive_log_basename("task.txt") == "harness-log"
    assert derive_log_basename("/some/dir/task.txt") == "harness-log"
    # Suffixed task files (multiple variants sharing one flat --workdir) get
    # distinct logs instead of silently overwriting each other
    assert derive_log_basename("task-b.txt") == "harness-log-b"
    assert derive_log_basename("task-c.txt") == "harness-log-c"
    # Non-"task"-prefixed filenames still get a distinguishing suffix
    assert derive_log_basename("brief.txt") == "harness-log-brief"
    # Explicit --log-prefix always wins
    assert derive_log_basename("task-b.txt", log_prefix="custom") == "custom"

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        # Allowed binary, exit 0
        r = execute_command(workdir, "python3 -c 'print(1)'", ["python3"], timeout=10)
        assert r["status"] == "ok" and r["exit_code"] == 0, r

        # Allowed binary, nonzero exit — this is the case --verify-command relies on
        r = execute_command(workdir, "python3 -c 'import sys; sys.exit(1)'", ["python3"], timeout=10)
        assert r["status"] == "ok" and r["exit_code"] == 1, r

        # Binary not in allowlist — rejected before it ever runs
        r = execute_command(workdir, "curl https://example.com", ["python3"], timeout=10)
        assert r["status"] == "error" and "not in the allowed" in r["message"], r

        # Timeout is enforced
        r = execute_command(workdir, "python3 -c 'import time; time.sleep(5)'", ["python3"], timeout=1)
        assert r["status"] == "error" and "timed out" in r["message"], r

        # Runs inside workdir, not the caller's cwd
        (workdir / "marker.txt").write_text("x")
        r = execute_command(workdir, "python3 -c 'import pathlib; print(pathlib.Path(\"marker.txt\").exists())'", ["python3"], timeout=10)
        assert r["stdout"].strip() == "True", r

        # Regression check: the model calling a .py script directly (no "python3 "
        # prefix) — observed 5+ times across real runs, always self-corrected the next
        # turn at the cost of a wasted turn. Auto-prepend python3 instead of rejecting.
        script = workdir / "checker.py"
        script.write_text("import sys; sys.exit(0)")
        r = execute_command(workdir, f"{script} arg1", ["python3"], timeout=10)
        assert r["status"] == "ok" and r["exit_code"] == 0, r
        # Still rejected if python3 itself isn't allowed — auto-correct only kicks in
        # when the obvious fix (python3) is actually available.
        r = execute_command(workdir, f"{script} arg1", ["node"], timeout=10)
        assert r["status"] == "error" and "not in the allowed" in r["message"], r
        # A non-.py unknown binary is never auto-corrected — this isn't a general
        # "guess what they meant" feature, just the one observed real pattern.
        r = execute_command(workdir, "curl https://example.com", ["python3"], timeout=10)
        assert r["status"] == "error" and "not in the allowed" in r["message"], r

    # The opt-in bootstrap exposes only a bounded, sorted relative inventory.
    # In particular, it never follows (or even reports) symlinks that could point
    # outside --workdir.
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
        workdir = Path(tmp)
        outside = Path(outside_tmp)
        (workdir / "zeta.py").write_text("not sent to the model")
        (workdir / "alpha").mkdir()
        (workdir / "alpha" / "nested.py").write_text("not sent either")
        (workdir / "test_greet.py").write_text("secret-looking fixture content")
        (outside / "outside-secret.txt").write_text("must not be exposed")
        (workdir / "escape").symlink_to(outside, target_is_directory=True)
        inventory = workspace_inventory(workdir, limit=20)
        assert inventory["entries"] == sorted(inventory["entries"]), inventory
        assert all(not Path(path.rstrip("/")).is_absolute() for path in inventory["entries"]), inventory
        assert "test_greet.py" in inventory["entries"], inventory
        assert "escape/" not in inventory["entries"] and "outside-secret.txt" not in inventory["entries"], inventory
        bounded = workspace_inventory(workdir, limit=2)
        assert len(bounded["entries"]) <= 2 and bounded["truncated"], bounded
        bootstrap = build_workspace_bootstrap(workdir, 20, "python3 -m pytest -q")
        assert "test_greet.py" in bootstrap and "secret-looking fixture content" not in bootstrap, bootstrap
        assert "read-only harness configuration" in bootstrap and "python3 -m pytest -q" in bootstrap, bootstrap

    # Bootstrap is present in the very first model request, before it can take a
    # tool action. A root-level test file mirrors the seeded Python Coder fixture.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        (workdir / "greet.py").write_text("def greet(): pass")
        (workdir / "test_greet.py").write_text("def test_greet(): pass")
        captured_messages = []
        result = _run_once_with_responses(
            workdir, [_tool_response("finish", {"summary": "done"})],
            "python3 -c 'import sys; sys.exit(0)'", captured_messages=captured_messages,
            workspace_bootstrap=True, workspace_inventory_limit=20,
            bootstrap_include_verify_command=True,
        )
        assert result["finished"], result
        first_request = captured_messages[0]
        assert first_request[-1]["role"] == "user", first_request
        assert "test_greet.py" in first_request[-1]["content"], first_request
        assert "read-only harness configuration" in first_request[-1]["content"], first_request

    # Repeatedly failing the exact same command produces explicit, logged model
    # feedback with the actual cwd and an instruction to inspect before retrying.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        captured_messages = []
        command_tools = [*TOOLS, qwen_agent.make_run_command_tool(["python3"])]
        result = _run_once_with_responses(
            workdir,
            [
                _tool_response("run_command", {"command": "python3 -c 'import sys; sys.exit(1)'"}, "run_1"),
                _tool_response("run_command", {"command": "python3 -c 'import sys; sys.exit(1)'"}, "run_2"),
                _tool_response("finish", {"summary": "stopped retrying"}, "finish"),
            ],
            None, captured_messages=captured_messages, tools=command_tools,
            repeated_command_failure_threshold=2,
        )
        assert result["finished"], result
        feedback = json.loads(captured_messages[2][-1]["content"])["repeat_failure_feedback"]
        assert str(workdir.resolve()) in feedback and "list_dir" in feedback, feedback

    # run_once: model calls finish(), --verify-command passes -> finished, no crash.
    # This is the "sample passed" case the --samples aggregation loop relies on.
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_once_with_fake_model(Path(tmp), "python3 -c 'import sys; sys.exit(0)'")
        assert result["finished"] is True and result["crashed"] is None, result
        assert (Path(tmp) / "harness-log.json").exists()

    # run_once: model calls finish(), but --verify-command fails -> the harness
    # overrides the model's own claim. This is exactly what makes a sample "FAIL"
    # in the --samples summary even though the model itself thought it was done.
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_once_with_fake_model(Path(tmp), "python3 -c 'import sys; sys.exit(1)'")
        assert result["finished"] is False and result["crashed"] is not None, result

    # A prose-only response with a verifier is an actionable failed attempt, not
    # an unverified success. Its failure cause and initial verifier run are kept
    # in artifacts for a later recovery-enabled invocation to inspect.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        result = _run_once_with_responses(
            workdir,
            [{"choices": [{"finish_reason": "stop", "message": {"content": "Apply this patch yourself."}}]}],
            "python3 -c 'import sys; sys.exit(1)'",
        )
        assert not result["finished"] and result["initial_failure"]["cause"] == "no_tool_prose", result
        assert [item["phase"] for item in result["verification_results"]] == ["initial"], result
        artifact = json.loads((workdir / "harness-log.json").read_text())
        assert artifact["initial_failure"]["cause"] == "no_tool_prose", artifact
        assert artifact["recovery"]["enabled"] is False, artifact

    # Calling finish before any edit also remains an initial verifier failure;
    # recovery is deliberately disabled by default.
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_once_with_responses(
            Path(tmp), [_tool_response("finish", {"summary": "done"})],
            "python3 -c 'import sys; sys.exit(1)'",
        )
        assert not result["finished"] and result["initial_failure"]["cause"] == "verification_failed", result
        assert result["initial_failure"]["completion_kind"] == "finish", result
        assert result["recovery_attempts"] == 0, result

    # Recovery receives the harness's actual verifier result, edits through the
    # normal path-protected tools, then passes only after a second harness-owned
    # verification exits zero.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        captured_messages = []
        result = _run_once_with_responses(
            workdir,
            [
                _tool_response("finish", {"summary": "initially done"}, "finish_initial"),
                _tool_response("write_file", {"path": "fixed.txt", "content": "fixed"}, "write_recovery"),
                _tool_response("finish", {"summary": "fixed"}, "finish_recovery"),
            ],
            'python3 -c "import pathlib, sys; sys.exit(0 if pathlib.Path(\'fixed.txt\').exists() else 1)"',
            recovery_turns=2,
            captured_messages=captured_messages,
        )
        assert result["finished"] and result["crashed"] is None, result
        assert [item["passed"] for item in result["verification_results"]] == [False, True], result
        assert result["recovery_attempts"] == 2 and (workdir / "fixed.txt").read_text() == "fixed", result
        recovery_prompt = captured_messages[1][-1]["content"]
        assert "RECOVERY REQUIRED" in recovery_prompt and "Verifier result:" in recovery_prompt, recovery_prompt

    # A recovery completion that does not fix the verifier remains a failed
    # sample; a model finish call never overrides a nonzero checker result.
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_once_with_responses(
            Path(tmp),
            [
                _tool_response("finish", {"summary": "initially done"}, "finish_initial"),
                _tool_response("finish", {"summary": "still done"}, "finish_recovery"),
            ],
            "python3 -c 'import sys; sys.exit(1)'",
            recovery_turns=1,
        )
        assert not result["finished"] and "after recovery" in result["crashed"], result
        assert [item["passed"] for item in result["verification_results"]] == [False, False], result

    # Re-verification is only allowed after a subsequent completion. A recovery
    # tool call with no finish at its bounded turn limit cannot turn a sample into
    # a pass, even if that edit would have satisfied the checker.
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_once_with_responses(
            Path(tmp),
            [
                _tool_response("finish", {"summary": "initially done"}, "finish_initial"),
                _tool_response("write_file", {"path": "fixed.txt", "content": "fixed"}, "write_recovery"),
            ],
            'python3 -c "import pathlib, sys; sys.exit(0 if pathlib.Path(\'fixed.txt\').exists() else 1)"',
            recovery_turns=1,
        )
        assert not result["finished"] and result["recovery_turn_limit_reached"], result
        assert len(result["verification_results"]) == 1, result

    # Regression check for the real failure found in Variant C sample 4/5: a model
    # facing a persistently failing self-check wrote its own always-passing stub over
    # the checker script instead of fixing its own (wrong) path to it. _targets_verify_script
    # is the detector _run_once's write_file dispatch uses to block that write outright.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        checker = workdir / "check_structure.py"
        checker.write_text("# real checker")
        verify_cmd = f"python3 {checker} output.html"
        assert _targets_verify_script(workdir, "check_structure.py", verify_cmd) is True
        assert _targets_verify_script(workdir, "output.html", verify_cmd) is False
        assert _targets_verify_script(workdir, "check_structure.py", None) is False

    # destructive_reason: flags the patterns the propose/confirm gate exists for,
    # leaves ordinary commands alone.
    assert destructive_reason("python3 -c \"import shutil; shutil.rmtree('x')\"") is not None
    assert destructive_reason("python3 -c \"import os; os.remove('x')\"") is not None
    assert destructive_reason("python3 -c \"Path('x').unlink()\"") is not None
    assert destructive_reason("git reset --hard HEAD~1") is not None
    assert destructive_reason("git clean -fd") is not None
    assert destructive_reason("git push --force origin main") is not None
    assert destructive_reason("git branch -D old-branch") is not None
    assert destructive_reason("pytest -q tests/") is None
    assert destructive_reason("git status") is None
    assert destructive_reason("git diff --cached") is None

    # execute_command: a destructive command is blocked, not run -- recorded as a
    # pending proposal instead, target file untouched.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        target = workdir / "keep-me.txt"
        target.write_text("still here")
        cmd = f"python3 -c \"import os; os.remove('{target}')\""
        r = execute_command(workdir, cmd, ["python3"], timeout=10)
        assert r["status"] == "pending_confirmation" and "token" in r, r
        assert target.exists(), "destructive command must not actually run"
        pending_path = workdir / PENDING_ACTION_FILENAME
        assert pending_path.exists()
        pending = json.loads(pending_path.read_text())
        assert pending["command"] == cmd and pending["token"] == r["token"]

        # resolve_pending_action: wrong token refuses, leaves the pending file intact
        # so a later correct attempt can still succeed.
        wrong = resolve_pending_action(workdir, "not-the-token", ["python3"], timeout=10)
        assert wrong["status"] == "error" and "does not match" in wrong["message"], wrong
        assert target.exists() and pending_path.exists()

        # resolve_pending_action: an expired proposal is refused even with the right
        # token, and the stale pending file is cleared either way.
        stale = json.loads(pending_path.read_text())
        stale["proposed_at"] = "2000-01-01T00:00:00+00:00"
        pending_path.write_text(json.dumps(stale))
        expired = resolve_pending_action(workdir, stale["token"], ["python3"], timeout=10)
        assert expired["status"] == "error" and "expired" in expired["message"], expired
        assert target.exists(), "expired approval must not execute"
        assert not pending_path.exists(), "pending file must be cleared after resolving, even on refusal"

        # Re-propose (pending file was cleared above), then approve with the right,
        # non-expired token -- this time it actually runs, and the file is cleared.
        r2 = execute_command(workdir, cmd, ["python3"], timeout=10)
        approved = resolve_pending_action(workdir, r2["token"], ["python3"], timeout=10)
        assert approved["status"] == "ok" and approved["exit_code"] == 0, approved
        assert not target.exists(), "approved action must actually execute"
        assert not pending_path.exists()

        # No pending file at all -- clean error, not a crash.
        missing = resolve_pending_action(workdir, "whatever", ["python3"], timeout=10)
        assert missing["status"] == "error" and "no pending action" in missing["message"], missing

    print("all checks passed")


if __name__ == "__main__":
    demo()
