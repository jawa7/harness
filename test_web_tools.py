#!/usr/bin/env python3
"""Self-check for the --enable-web-access tools (web_search, web_fetch, html_to_text).

No real network call — urllib.request.urlopen is monkeypatched to a canned response,
same pattern as test_qwen_agent.py's call_model monkeypatching.
Run: python3 test_web_tools.py
"""
import json
import os
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import qwen_agent
from qwen_agent import html_to_text, tool_web_fetch, tool_web_search, check_tavily_key_or_die, run_once, WEB_TOOLS, extract_urls


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def demo():
    # html_to_text: strips tags and script/style content, keeps visible text
    html = "<html><head><style>.x{}</style></head><body><p>Hello</p><script>evil()</script>World</body></html>"
    text = html_to_text(html)
    assert "Hello" in text and "World" in text, text
    assert "evil()" not in text and ".x{}" not in text, text

    # web_search: no TAVILY_API_KEY set -> clean error, not a crash
    with patch.dict(os.environ, {}, clear=True):
        result = tool_web_search(Path("."), {"query": "test"})
        assert result["status"] == "error" and "TAVILY_API_KEY" in result["message"], result

    # web_search: happy path, mocked Tavily response
    fake_body = json.dumps({
        "results": [
            {"title": "A", "url": "https://a.example", "content": "content A"},
            {"title": "B", "url": "https://b.example", "content": "content B"},
        ]
    }).encode("utf-8")
    with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-fake"}), \
         patch("urllib.request.urlopen", return_value=_FakeResponse(fake_body, {})):
        result = tool_web_search(Path("."), {"query": "test"})
        assert result["status"] == "ok" and len(result["results"]) == 2, result
        assert result["results"][0]["url"] == "https://a.example", result

    # web_search: missing query arg -> error, not a KeyError crash
    result = tool_web_search(Path("."), {})
    assert result["status"] == "error", result

    # web_fetch: rejects non-http(s) urls before ever touching the network
    result = tool_web_fetch(Path("."), {"url": "file:///etc/passwd"})
    assert result["status"] == "error", result

    # web_fetch: happy path, HTML gets stripped to plain text
    fake_html = b"<html><body><p>Real content here</p></body></html>"
    with patch("urllib.request.urlopen", return_value=_FakeResponse(fake_html, {"Content-Type": "text/html; charset=utf-8"})):
        result = tool_web_fetch(Path("."), {"url": "https://example.com"})
        assert result["status"] == "ok", result
        assert "Real content here" in result["text"], result
        assert "<p>" not in result["text"], result

    # web_fetch: rejects unsupported content-type (e.g. a binary download) instead of
    # returning raw bytes as if they were text
    with patch("urllib.request.urlopen", return_value=_FakeResponse(b"\x89PNG...", {"Content-Type": "image/png"})):
        result = tool_web_fetch(Path("."), {"url": "https://example.com/x.png"})
        assert result["status"] == "error" and "content-type" in result["message"], result

    # web_fetch: truncates oversized content rather than returning it unbounded
    huge = ("x" * (qwen_agent.MAX_FETCH_CHARS + 1000)).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(huge, {"Content-Type": "text/plain"})):
        result = tool_web_fetch(Path("."), {"url": "https://example.com/big.txt"})
        assert result["truncated"] is True, result
        assert len(result["text"]) == qwen_agent.MAX_FETCH_CHARS, result

    # check_tavily_key_or_die: pre-flight check exits with a distinct code (not 1/2/3,
    # already used by turn-exhaustion/crash) when the key is missing
    with patch.dict(os.environ, {}, clear=True):
        try:
            check_tavily_key_or_die()
            assert False, "should have exited"
        except SystemExit as e:
            assert e.code == 4, e.code

    # extract_urls: a comma right after a URL (a real, live-observed pattern — "Fetched:
    # <url>, <url2>") must not stick to the URL and break equality against the same URL
    # recorded from an actual web_fetch call (which has no trailing punctuation).
    assert extract_urls("Fetched URLs: https://a.example/x, https://b.example/y") == {
        "https://a.example/x", "https://b.example/y",
    }
    assert extract_urls("See https://a.example/x.") == {"https://a.example/x"}

    # run_once: the model actually web_fetches one URL but claims (in its saved output) to
    # have used two — the harness must catch the unverified one mechanically, not trust the
    # claim (same "never trust self-cert" principle as --verify-command). Comma placed
    # directly after the fetched URL, no space — this is the exact live-observed shape that
    # first broke the naive regex (the fetched URL was wrongly flagged as unverified too).
    fetched_url = "https://real.example/fetched"
    unfetched_url = "https://real.example/never-fetched"

    def scripted_fake_call_model(base_url, model, messages, temperature, max_tokens, tools):
        turn = sum(1 for m in messages if m["role"] == "assistant") + 1
        if turn == 1:
            call = {"name": "web_fetch", "arguments": json.dumps({"url": fetched_url})}
        elif turn == 2:
            content = f"Fetched URLs: {fetched_url}, {unfetched_url}"
            call = {"name": "write_file", "arguments": json.dumps({"path": "out.md", "content": content})}
        else:
            call = {"name": "finish", "arguments": json.dumps({"summary": "done"})}
        return {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"content": "", "tool_calls": [{"id": f"call_{turn}", "function": call}]},
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    orig_call_model = qwen_agent.call_model
    qwen_agent.call_model = scripted_fake_call_model
    try:
        with tempfile.TemporaryDirectory() as tmp, \
             patch("urllib.request.urlopen", return_value=_FakeResponse(b"<p>ok</p>", {"Content-Type": "text/html"})):
            result = run_once(
                workdir=Path(tmp), system_prompt="sys", task_prompt="task",
                tools=WEB_TOOLS, allowed_tool_names={t["function"]["name"] for t in WEB_TOOLS},
                allowed_bins=[], model="fake-model", base_url="http://unused",
                temperature=0.0, max_tokens=100, max_turns=5,
                command_timeout=5,
                verify_command=None, verify_timeout=5, log_basename="harness-log",
            )
            assert result["finished"] is True, result
            assert result["unverified_urls"] == [unfetched_url], result
            log_json = json.loads((Path(tmp) / "harness-log.json").read_text())
            assert log_json["unverified_urls"] == [unfetched_url], log_json
    finally:
        qwen_agent.call_model = orig_call_model

    print("all checks passed")


if __name__ == "__main__":
    demo()
