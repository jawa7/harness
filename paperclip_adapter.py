#!/usr/bin/env python3
"""Thin glue between paperclip's built-in `process` adapter and qwen_agent.py.

paperclip's `process` adapter (already implemented server-side, just hidden
from its UI picker — see `execute.js`) runs an arbitrary command per agent
run and injects PAPERCLIP_RUN_ID / PAPERCLIP_API_URL / PAPERCLIP_API_KEY /
PAPERCLIP_WORKSPACE_CWD into its environment. This script uses that context
to fetch the run's assigned issue, hand it to qwen_agent.py as a task, and
post the result back — so a paperclip agent configured with:

    adapterType: process
    command: python3
    args: ["/absolute/path/to/paperclip_adapter.py"]

runs qwen (via LM Studio) against real paperclip issues, same as any other
agent's adapter, with no custom adapter plugin needed.

Propose/confirm/execute gate: qwen_agent.py refuses to run an irreversible
command itself (see its DESTRUCTIVE_PATTERNS) and instead records a pending
proposal (<workdir>/pending-action.json) with a random token. This script
checks for that file on every run, before doing anything else:
  - a human reply "APPROVE <token>" on the issue -> re-invoke qwen_agent.py
    with --resolve-pending to execute exactly that stored command (the model
    is not asked again -- approval is for the proposed string, not a new
    decision)
  - "REJECT <token>" -> acknowledge and clear the pending state, no execution
  - neither yet -> do nothing this run, stay blocked

stdlib-only, matching qwen_agent.py's own no-deps philosophy.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

QWEN_AGENT = Path(__file__).parent / "qwen_agent.py"
DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "qwen2.5-coder-7b-instruct-mlx"
ALLOWED_COMMANDS = "npm,npx,python3,pytest,git"
PENDING_ACTION_FILENAME = "pending-action.json"

SYSTEM_PROMPT = """You are a coding agent working inside paperclip, an autonomous \
multi-agent platform. You have been assigned one task (below). Do the work \
directly in the working directory using your write_file/read_file/list_dir \
tools, then call finish() when done. Be concrete: make the actual changes, \
don't just describe them."""


def api_request(method: str, path: str, api_url: str, api_key: str, body: dict | None = None) -> dict:
    url = f"{api_url.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def post_comment(issue_id: str, api_url: str, api_key: str, body: str) -> None:
    try:
        api_request("POST", f"/api/issues/{issue_id}/comments", api_url, api_key, {"body": body})
    except Exception as exc:  # noqa: BLE001 - best-effort, never mask the real exit code over this
        print(f"paperclip_adapter: failed to post comment: {exc}", file=sys.stderr)


def set_issue_status(issue_id: str, api_url: str, api_key: str, status: str) -> None:
    try:
        api_request("PATCH", f"/api/issues/{issue_id}", api_url, api_key, {"status": status})
    except Exception as exc:  # noqa: BLE001
        print(f"paperclip_adapter: failed to set issue status to {status}: {exc}", file=sys.stderr)


def find_human_decision(issue_id: str, api_url: str, api_key: str, token: str) -> str | None:
    """Scans this issue's comments for a human ("authorType": "user") reply matching
    "APPROVE <token>" or "REJECT <token>" (case-insensitive on the keyword, exact on
    the token). Returns "approve", "reject", or None if neither has arrived yet."""
    comments = api_request("GET", f"/api/issues/{issue_id}/comments?order=desc&limit=50", api_url, api_key)
    approve_re = re.compile(rf"\bAPPROVE\s+{re.escape(token)}\b", re.IGNORECASE)
    reject_re = re.compile(rf"\bREJECT\s+{re.escape(token)}\b", re.IGNORECASE)
    for comment in comments:
        if comment.get("authorType") != "user":
            continue
        body = comment.get("body") or ""
        if approve_re.search(body):
            return "approve"
        if reject_re.search(body):
            return "reject"
    return None


def handle_pending_action(pending_path: Path, workdir: str, issue_id: str, api_url: str, api_key: str) -> int:
    """A previous run left a pending destructive-action proposal. Check whether a
    human has approved or rejected it yet; execute, clear, or do nothing accordingly.
    Returns the process exit code."""
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    token = pending["token"]
    decision = find_human_decision(issue_id, api_url, api_key, token)

    if decision is None:
        print(f"paperclip_adapter: still awaiting approval for pending action (token {token})", file=sys.stderr)
        return 0

    if decision == "reject":
        pending_path.unlink(missing_ok=True)
        post_comment(issue_id, api_url, api_key,
                     f"Proposal rejected. Not executing `{pending['command']}`. Re-investigating on next run.")
        return 0

    # decision == "approve" -- resolve_pending_action (inside qwen_agent.py) re-validates
    # the token and expiry itself; this script doesn't duplicate that check.
    cmd = [
        sys.executable, str(QWEN_AGENT),
        "--workdir", workdir,
        "--resolve-pending", token,
        "--allowed-commands", ALLOWED_COMMANDS,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        outcome = json.loads(result.stdout)
    except json.JSONDecodeError:
        outcome = {"status": "error", "message": result.stdout or result.stderr}

    if outcome.get("status") == "ok" and outcome.get("exit_code") == 0:
        post_comment(issue_id, api_url, api_key,
                     f"Approved action executed: `{pending['command']}`\n\n```\n{outcome}\n```")
    else:
        post_comment(issue_id, api_url, api_key,
                     f"Approved action did NOT complete cleanly: `{pending['command']}`\n\n```\n{outcome}\n```")
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    return 0 if outcome.get("status") == "ok" and outcome.get("exit_code") == 0 else 1


def resolve_workdir() -> str:
    """PAPERCLIP_WORKSPACE_CWD is supposed to be injected by the process adapter, but
    has been observed missing/empty on some runs -- falling back to os.getcwd() is
    unsafe because that can be the server process's own cwd (e.g. "/" on macOS,
    which is read-only and crashes qwen_agent.py deep inside write_logs() with a
    confusing OSError instead of a clear error). Prefer the run's dedicated scratch
    dirs (always writable, always injected) before ever touching bare cwd, and
    refuse root outright."""
    for var in ("PAPERCLIP_WORKSPACE_CWD", "PAPERCLIP_RUN_SCRATCH_DIR", "PAPERCLIP_SCRATCH_DIR"):
        value = os.environ.get(var)
        if value and value not in ("/", ""):
            return value
    cwd = os.getcwd()
    if cwd not in ("/", ""):
        return cwd
    return tempfile.mkdtemp(prefix="paperclip-qwen-workdir-")


def main() -> int:
    run_id = os.environ.get("PAPERCLIP_RUN_ID")
    api_url = os.environ.get("PAPERCLIP_API_URL")
    api_key = os.environ.get("PAPERCLIP_API_KEY")
    workdir = resolve_workdir()

    if not (run_id and api_url and api_key):
        print("paperclip_adapter: missing PAPERCLIP_RUN_ID/API_URL/API_KEY — "
              "not running under paperclip's process adapter?", file=sys.stderr)
        return 2

    # 1. Resolve which issue this run is for.
    issues = api_request("GET", f"/api/heartbeat-runs/{run_id}/issues", api_url, api_key)
    if not issues:
        print(f"paperclip_adapter: run {run_id} has no assigned issue", file=sys.stderr)
        return 2
    issue_id = issues[0]["issueId"]
    issue = api_request("GET", f"/api/issues/{issue_id}", api_url, api_key)

    # 1b. A prior run may have left a proposal awaiting human approval/rejection —
    # handle that before considering any fresh investigation.
    pending_path = Path(workdir) / PENDING_ACTION_FILENAME
    if pending_path.is_file():
        return handle_pending_action(pending_path, workdir, issue_id, api_url, api_key)

    task_text = f"# {issue['title']}\n\n{issue.get('description') or ''}"

    # 2. Write system + task files qwen_agent.py expects as real files.
    with tempfile.TemporaryDirectory(prefix="paperclip-qwen-") as tmp:
        system_path = Path(tmp) / "system.txt"
        task_path = Path(tmp) / "task.txt"
        system_path.write_text(SYSTEM_PROMPT)
        task_path.write_text(task_text)

        model = os.environ.get("QWEN_MODEL", DEFAULT_MODEL)
        base_url = os.environ.get("QWEN_BASE_URL", DEFAULT_BASE_URL)

        cmd = [
            sys.executable, str(QWEN_AGENT),
            "--workdir", workdir,
            "--system", str(system_path),
            "--task", str(task_path),
            "--model", model,
            "--base-url", base_url,
            "--enable-run-command",
            "--allowed-commands", ALLOWED_COMMANDS,
            "--log-prefix", f"paperclip-run-{run_id[:8]}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

    # 3. A destructive command was proposed instead of run (exit 4) -- post the
    # proposal for human review and block the issue, instead of the normal
    # succeeded/failed comment.
    if result.returncode == 4 and pending_path.is_file():
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        post_comment(issue_id, api_url, api_key,
                     f"⏸️ Proposed action pending confirmation: `{pending['command']}`\n\n"
                     f"Reason: {pending['reason']}\n\n"
                     f"Reply exactly `APPROVE {pending['token']}` to execute, "
                     f"or `REJECT {pending['token']}` to cancel.")
        set_issue_status(issue_id, api_url, api_key, "blocked")
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        return 0

    success = result.returncode == 0
    summary = (
        f"qwen_agent.py {'succeeded' if success else 'failed'} "
        f"(exit {result.returncode}). Model: {model} via {base_url}.\n\n"
        f"```\n{(result.stdout or '')[-3000:]}\n{(result.stderr or '')[-1500:]}\n```"
    )

    # 4. Post the outcome back as an issue comment so it's visible in paperclip's UI.
    post_comment(issue_id, api_url, api_key, summary)

    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
