#!/usr/bin/env python3
"""Self-check for paperclip_adapter.py's propose/confirm/execute handling.

No live paperclip server or LM Studio involved for the API-facing pieces —
api_request is monkeypatched to canned responses. The actual approved-action
execution path does shell out to the real qwen_agent.py --resolve-pending
(no network call in that code path either), so it's exercised for real.
Run: python3 test_paperclip_adapter.py
"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import paperclip_adapter
from paperclip_adapter import find_human_decision, handle_pending_action, PENDING_ACTION_FILENAME


def _fake_api_request_factory(comments):
    calls = []

    def fake(method, path, api_url, api_key, body=None):
        calls.append((method, path, body))
        if method == "GET" and "/comments" in path:
            return comments
        if method == "POST" and "/comments" in path:
            return {"id": "fake-comment-id"}
        if method == "PATCH":
            return {"id": "fake-issue-id", "status": (body or {}).get("status")}
        raise AssertionError(f"unexpected api_request call: {method} {path}")

    return fake, calls


def _comment(body, author_type="user"):
    return {"body": body, "authorType": author_type}


def demo():
    # find_human_decision: exact token match required, case-insensitive keyword,
    # only "user"-authored comments count (an agent echoing "APPROVE <token>" back
    # into its own summary must never self-approve).
    comments = [
        _comment("just chatting, nothing relevant"),
        _comment("APPROVE deadbeef", author_type="agent"),  # not from a human -- ignored
        _comment("approve deadbeef"),  # case-insensitive keyword is fine
    ]
    fake, calls = _fake_api_request_factory(comments)
    paperclip_adapter.api_request = fake
    assert find_human_decision("issue-1", "http://api", "key", "deadbeef") == "approve"

    fake, calls = _fake_api_request_factory([_comment("REJECT cafef00d please, wrong target")])
    paperclip_adapter.api_request = fake
    assert find_human_decision("issue-1", "http://api", "key", "cafef00d") == "reject"

    fake, calls = _fake_api_request_factory([_comment("APPROVE deadbeef")])
    paperclip_adapter.api_request = fake
    # Different token in flight -- an unrelated approval must not match.
    assert find_human_decision("issue-1", "http://api", "key", "cafef00d") is None

    fake, calls = _fake_api_request_factory([])
    paperclip_adapter.api_request = fake
    assert find_human_decision("issue-1", "http://api", "key", "deadbeef") is None

    # handle_pending_action: no decision yet -- no comment posted, no execution,
    # pending file left untouched for the next run to check again.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        pending_path = workdir / PENDING_ACTION_FILENAME
        pending_path.write_text(json.dumps({
            "command": "python3 -c \"pass\"", "reason": "test", "token": "tok123",
            "proposed_at": "2026-01-01T00:00:00+00:00",
        }))
        fake, calls = _fake_api_request_factory([_comment("no decision here")])
        paperclip_adapter.api_request = fake
        rc = handle_pending_action(pending_path, str(workdir), "issue-1", "http://api", "key")
        assert rc == 0
        assert pending_path.exists(), "must stay pending until a decision arrives"
        assert not any(call[0] == "POST" for call in calls), "must not comment while still waiting"

    # handle_pending_action: reject -- clears the file, posts one acknowledgement,
    # never shells out to qwen_agent.py.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        pending_path = workdir / PENDING_ACTION_FILENAME
        pending_path.write_text(json.dumps({
            "command": "python3 -c \"pass\"", "reason": "test", "token": "tok123",
            "proposed_at": "2026-01-01T00:00:00+00:00",
        }))
        fake, calls = _fake_api_request_factory([_comment("REJECT tok123")])
        paperclip_adapter.api_request = fake
        rc = handle_pending_action(pending_path, str(workdir), "issue-1", "http://api", "key")
        assert rc == 0
        assert not pending_path.exists()
        post_calls = [c for c in calls if c[0] == "POST"]
        assert len(post_calls) == 1 and "rejected" in post_calls[0][2]["body"].lower()

    # handle_pending_action: approve -- actually shells out to the real
    # qwen_agent.py --resolve-pending, which executes the stored command for real.
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        target = workdir / "victim.txt"
        target.write_text("delete me")
        pending_path = workdir / PENDING_ACTION_FILENAME
        # proposed_at must be recent -- qwen_agent's resolve_pending_action refuses
        # anything older than its 48h window, so a fixed past date would eventually
        # make this test start failing on its own as time moves on.
        pending_path.write_text(json.dumps({
            "command": f"python3 -c \"import os; os.remove('{target}')\"",
            "reason": "test", "token": "tok456",
            "proposed_at": datetime.now(timezone.utc).isoformat(),
        }))

        fake, calls = _fake_api_request_factory([_comment("APPROVE tok456")])
        paperclip_adapter.api_request = fake
        rc = handle_pending_action(pending_path, str(workdir), "issue-1", "http://api", "key")
        assert rc == 0, rc
        assert not target.exists(), "approved command must actually execute"
        assert not pending_path.exists()
        post_calls = [c for c in calls if c[0] == "POST"]
        assert len(post_calls) == 1 and "executed" in post_calls[0][2]["body"].lower()

    print("all checks passed")


if __name__ == "__main__":
    demo()
