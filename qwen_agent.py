#!/usr/bin/env python3
"""Minimal reusable tool-calling harness for local LM Studio models.

Runs a bounded agent loop against an OpenAI-compatible /v1/chat/completions
endpoint: send system+task prompt with tool definitions, execute any tool
calls for real (sandboxed to --workdir), feed results back, repeat until the
model calls finish(), returns plain text with no tool call, or --max-turns
is hit.

Tools: write_file, read_file, list_dir, update_plan, finish, and optionally
run_command (--enable-run-command) for the model to self-check its work (run
tests, a render script, etc.) mid-loop. update_plan lets the model declare
subtasks and record failed attempts; its latest call is re-shown to the model
every turn as a compact reminder, separate from the raw growing transcript.

--enable-web-access switches to a dedicated restricted mode (write_file,
read_file, list_dir, finish, web_search, web_fetch — no run_command, not
combined with the normal mode's tools) for gathering real material from the
web. web_search calls the Tavily API (requires TAVILY_API_KEY in the
environment, checked before the run starts). web_fetch downloads a URL and
returns its text content (HTML stripped to plain text), capped in size.
Mutually exclusive with --reviewer-mode.

Self-certification isn't trusted: if --verify-command is set, the harness
itself re-runs that command after a model completion and overrides success if
it fails — the model's claim of "done" is never the last word. --recovery-turns
can opt into a bounded correction loop after that initial failure.

Usage:
    python3 qwen_agent.py --workdir DIR --system FILE --task FILE
        [--max-turns 10] [--model qwen/qwen2.5-coder-14b]
        [--base-url http://localhost:1234/v1] [--temperature 0.4]
        [--enable-run-command] [--allowed-commands pytest,python3,node,npm,bd]
        [--verify-command "pytest -q"] [--recovery-turns 0]
        [--bootstrap-workspace-context] [--bootstrap-inventory-limit 200]

Every run writes <log-basename>.json (raw transcript, including per-turn token
usage) and <log-basename>.md (human-readable) into --workdir. log-basename
defaults to "harness-log" with a suffix derived from --task's filename (e.g.
"task-b.txt" -> "harness-log-b"), or set --log-prefix explicitly. This
matters when multiple variants share one flat --workdir (no per-variant
subfolder): without a distinct suffix, a second run silently overwrites the
first run's log with no error.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

# Same "never trust the model's own claim, verify mechanically" principle as
# --verify-command, applied to --enable-web-access: a URL the model writes into its
# output but never actually passed to web_fetch is a claim, not a fact.
URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')
_URL_TRAILING_PUNCT = ',.;:!?)]}\'"'


def extract_urls(text: str) -> set:
    """URL_RE matches greedily, so trailing prose punctuation right after a URL (a comma
    before the next item in a list, a period ending a sentence) gets swept into the match —
    found live: 'Fetched: <url>, <url2>' put a trailing comma on the first URL, so it never
    matched the same URL as recorded by an actual web_fetch call, producing a false
    "unverified" flag on a URL that WAS genuinely fetched. Strip it back off."""
    return {u.rstrip(_URL_TRAILING_PUNCT) for u in URL_RE.findall(text)}

# Observed repeatedly (4 separate real runs): after a failing run_command result, the
# model re-submits byte-identical write_file content on the next turn instead of adapting
# — not a rare fluke, a recurring non-adaptive pattern for this model size. Bail out early
# instead of burning the full turn budget on calls that will keep failing the same way.
REPEAT_THRESHOLD = 3
DEFAULT_WORKSPACE_INVENTORY_LIMIT = 200
DEFAULT_REPEATED_COMMAND_FAILURE_THRESHOLD = 3


def check_tavily_key_or_die():
    """Pre-flight check for --enable-web-access: fail loudly before the run starts
    rather than let web_search silently error on every call."""
    if not os.environ.get("TAVILY_API_KEY"):
        print(
            "REFUSING TO START: --enable-web-access requires TAVILY_API_KEY to be set in "
            "the environment (web_search calls Tavily's API). Set it and retry.",
            file=sys.stderr,
        )
        sys.exit(4)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write (or overwrite) a file at a path relative to the working directory. Creates parent directories as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path, e.g. 'output.html'"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file at a path relative to the working directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories at a path relative to the working directory (default: '.').",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": (
                "Declare or update your plan: the remaining subtasks and what you've already tried "
                "and why it failed. Always send the FULL current plan, not a diff — this call replaces "
                "your previous plan entirely. It stays visible to you on every later turn, so use it "
                "instead of retrying an approach you've already recorded as failed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": (
                            "Free-text plan: subtasks with status (done/pending), and any failed "
                            "attempts with why they failed. E.g. '1. [x] add validate() to models.py\\n"
                            "2. [ ] wire validate() into cli.py -- tried importing from service.py, "
                            "ImportError: no service module here, use models.py directly\\n3. [ ] run tests'"
                        ),
                    },
                },
                "required": ["plan"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Call this when the task is complete. Before calling it, verify the requested item count, "
                "URLs, dates, and output paths are present in your summary whenever the task asked for them."
            ),
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]

def make_run_command_tool(allowed_bins: list) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command inside the sandboxed working directory to check your "
                "work — e.g. run tests, a render/lint script. Only these binaries are allowed: "
                f"{', '.join(allowed_bins)}. Times out after a fixed limit. This is how you "
                "verify your own work before calling finish() — finish() alone is not trusted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Full command line, e.g. 'pytest -q tests/'"},
                },
                "required": ["command"],
            },
        },
    }


WRITE_VERDICT_TOOL = {
    "type": "function",
    "function": {
        "name": "write_verdict",
        "description": "Record your verdict on the ONE criterion you were asked to check. This is the ONLY way to report your result — you cannot edit the file you're reviewing.",
        "parameters": {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean", "description": "true if the fragment satisfies this criterion, false otherwise"},
                "note": {"type": "string", "description": "One sentence citing the actual markup/CSS you read — not a guess"},
            },
            "required": ["pass", "note"],
        },
    },
}

# Reviewer mode: read-only w.r.t. the file under review — no write_file, so a reviewer
# structurally cannot "fix" (and silently corrupt) the thing it's supposed to be grading.
# One criterion per call (write_verdict, not a multi-criterion write_findings array) —
# a flat {pass, note} object proved reliable where a nested criteria array did not.
REVIEWER_TOOLS = [t for t in TOOLS if t["function"]["name"] in ("read_file", "list_dir", "finish")] + [WRITE_VERDICT_TOOL]

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web via Tavily. Returns up to 5 results (title, url, short "
            "snippet). Use web_fetch on a promising result's url to get its full content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string", "maxLength": 300,
                    "description": "One concise search query, at most 300 characters",
                },
            },
            "required": ["query"],
        },
    },
}

WEB_FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch a URL's content as plain text (HTML tags stripped). The model receives "
            "an excerpt; when page-cache tools are enabled, the complete response is cached "
            "for save_fetched_page or print_fetched_page. Only http(s) URLs; only text/html, "
            "text/plain, or application/json content-types are accepted."
        ),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full URL including http:// or https://"}},
            "required": ["url"],
        },
    },
}

SAVE_FETCHED_PAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "save_fetched_page",
        "description": (
            "Save a page previously obtained with web_fetch from this session's cache. "
            "This never fetches the internet. With format 'both', path is a basename and "
            "creates <path>.raw and <path>.txt in one call. When asked to save both raw and "
            "text, use format 'both' exactly once; separate raw/text calls with the same path overwrite it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The previously fetched URL"},
                "path": {"type": "string", "description": "Output path or basename, relative to the working directory"},
                "format": {"type": "string", "enum": ["raw", "text", "both"]},
                "fetch_id": {"type": "string", "description": "Optional earlier snapshot ID; defaults to the latest fetch of this URL"},
            },
            "required": ["url", "path", "format"],
        },
    },
}

PRINT_FETCHED_PAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "print_fetched_page",
        "description": (
            "Print the complete cached page directly to the terminal, without generating or truncating it in the model. "
            "This never fetches the internet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The previously fetched URL"},
                "format": {"type": "string", "enum": ["raw", "text"]},
                "fetch_id": {"type": "string", "description": "Optional earlier snapshot ID; defaults to the latest fetch of this URL"},
            },
            "required": ["url", "format"],
        },
    },
}

# Web-access mode: a dedicated restricted mode (wayfinder ticket 01), not merged into the
# normal write/run tool set — no run_command alongside live network access. Keeps
# write_file/read_file/list_dir so the model can save fetched material to workdir for
# Claude to read afterward (the actual point: gathering material, not judging it alone).
WEB_TOOLS = [t for t in TOOLS if t["function"]["name"] in ("write_file", "read_file", "list_dir", "finish")] + [
    WEB_SEARCH_TOOL, WEB_FETCH_TOOL,
]

MAX_READ_CHARS = 20000
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
WEB_REQUEST_TIMEOUT = 15
MAX_FETCH_BYTES = 2_000_000
MAX_FETCH_CHARS = 8000
MAX_SEARCH_RESULTS = 5
MAX_WEB_REQUESTS_PER_CHAT_TURN = 12
MAX_WEB_SEARCH_QUERY_CHARS = 300

DEFAULT_CHAT_SYSTEM_PROMPT = """You are a helpful local coding agent.
Work carefully inside the selected working directory. Use only the tools provided
for this session. If the user supplies a URL, call web_fetch for that URL before
web_search; if that fetch succeeds, summarize it and call finish without further
web browsing. If a web operation fails after you already have a successful source,
use the available material for a partial answer and explain the limitation instead
of making more web calls. When asked to save or print a fetched page, use
save_fetched_page or print_fetched_page, not write_file; these operate on the
complete cached page. To save both raw and text page data, call
save_fetched_page once with format 'both'; do not make separate raw/text calls
using the same path, as that overwrites the first output. Explain results clearly, and call finish when the current
user request is complete. Before calling finish, check the user's requested
deliverables: required item count, URLs, dates, and file paths. If their source
URLs or details are already in tool results, include them in the final answer
without making another web request. Never output a bare JSON array shaped like tool calls
as prose: such a response is interpreted as a tool request by compatibility
adapters."""
CHAT_WEB_POLICY_PROMPT = """Tool policy: when the current user message contains one or more URLs,
call web_fetch for every supplied URL before calling web_search. Use web_search
only after each supplied URL fetch failed. If a supplied URL fetch succeeds, stop
web browsing and answer from it. If any web tool fails after at least one successful
web source, stop web browsing and answer from the material already collected; state
what could not be verified. At most 12 web_search/web_fetch calls are allowed per
user request. web_search takes one concise query of at most 300 characters."""
DEFAULT_CHAT_CONTEXT_MESSAGES = 20
# Leaves headroom under a 32K context for the system/policy prompt and one completion
# (default max_tokens=8000) -- see DESIGN.md Tier 2 item 1: the message-count window
# alone doesn't protect against one large read_file result blowing the budget.
DEFAULT_CHAT_CONTEXT_TOKENS = 20000
DEFAULT_CHAT_MAX_TURNS = 10
REPEATED_CHAT_TOOL_CALL_THRESHOLD = 3
LOOP_RECOVERY_EVIDENCE_CHARS = 6000
CHAT_SESSION_VERSION = 1
CHAT_TOOL_NAMES = (
    "read_file", "write_file", "list_dir", "run_command", "web_search", "web_fetch",
    "save_fetched_page", "print_fetched_page",
)


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text: drop script/style content, keep everything else's text.
    Parses the DOM via stdlib HTMLParser rather than regexing tags out of raw text —
    same lesson already learned the hard way for structural checks (see
    local_model_reliability_findings.md: regex-over-text gives false confidence)."""

    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.chunks.append(stripped)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    return "\n".join(extractor.chunks)


def resolve_safe_path(workdir: Path, rel_path: str) -> Path:
    candidate = (workdir / rel_path).resolve()
    workdir_resolved = workdir.resolve()
    try:
        candidate.relative_to(workdir_resolved)
    except ValueError:
        raise PermissionError(f"path '{rel_path}' escapes sandbox root {workdir_resolved}")
    return candidate


def workspace_inventory(workdir: Path, limit: int = DEFAULT_WORKSPACE_INVENTORY_LIMIT) -> dict:
    """Return a deterministic, bounded inventory of regular files and directories.

    This is deliberately metadata-only. Symlinks are omitted rather than followed so
    an inventory can neither reveal nor traverse a path outside the sandbox. The
    limit bounds both the returned context and the number of filesystem entries
    inspected, including on an unexpectedly large repository.
    """
    if limit < 1:
        raise ValueError("workspace inventory limit must be at least 1")
    root = workdir.resolve()
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {root}")

    entries = []
    pending = [root]
    inspected = 0
    truncated = False
    while pending and inspected < limit:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError:
            # A concurrently changed or unreadable directory is not useful model
            # context. Keep the inventory safe and deterministic for what we can read.
            continue
        child_directories = []
        for child in children:
            if inspected >= limit:
                truncated = True
                break
            inspected += 1
            # Do not expose symlink names or follow them, even when their current
            # target happens to be inside the workspace: target changes are a TOCTOU
            # path-escape risk and there is no coding benefit to listing them here.
            if child.is_symlink():
                continue
            try:
                relative = child.relative_to(root).as_posix()
                is_directory = child.is_dir()
            except (OSError, ValueError):
                continue
            entries.append(f"{relative}/" if is_directory else relative)
            if is_directory:
                child_directories.append(child)
        # Stack reverse order gives a stable depth-first traversal. Sort once below so
        # the promised output order never depends on traversal details.
        pending.extend(reversed(child_directories))
    if pending:
        truncated = True
    return {"entries": sorted(entries), "truncated": truncated, "limit": limit}


def build_workspace_bootstrap(
    workdir: Path, inventory_limit: int = DEFAULT_WORKSPACE_INVENTORY_LIMIT,
    verify_command: str | None = None,
) -> str:
    """Build the opt-in metadata context sent before the model's first action."""
    inventory = workspace_inventory(workdir, inventory_limit)
    lines = [
        "WORKSPACE CONTEXT (harness-generated; metadata only)",
        "The following paths are relative to the current workdir. Inspect files and directories before editing; do not assume a tests/ directory exists.",
        f"Inventory (sorted, first {len(inventory['entries'])} entries; limit {inventory['limit']}):",
    ]
    lines.extend(f"- {entry}" for entry in inventory["entries"])
    if inventory["truncated"]:
        lines.append("- [inventory truncated at the configured limit]")
    if verify_command:
        lines.extend([
            "",
            "HARNESS VERIFICATION ADVISORY (read-only harness configuration; do not edit it or any checker it invokes):",
            f"The harness, not you, will run this command after completion: {verify_command}",
        ])
    return "\n".join(lines)


def _targets_verify_script(workdir: Path, write_path: str, verify_command: str) -> bool:
    """True if write_path would overwrite a .py file that --verify-command invokes.
    verify_command's script path is typically OUTSIDE workdir on purpose (points at the
    shared project dir, not a per-sample sandbox — see the --samples absolute-path note),
    so this compares resolved absolute paths directly rather than going through
    resolve_safe_path (which would reject anything outside workdir as a sandbox escape)."""
    if not verify_command:
        return False
    try:
        write_target = (workdir / write_path).resolve()
    except (OSError, ValueError):
        return False
    try:
        verify_tokens = shlex.split(verify_command)
    except ValueError:
        return False
    for token in verify_tokens:
        if token.endswith(".py"):
            try:
                if Path(token).resolve() == write_target:
                    return True
            except (OSError, ValueError):
                continue
    return False


def _unescape_if_flattened(content: str) -> str:
    """Undo a JSON-encoding mistake observed repeatedly from this model class: instead of
    a real newline in write_file's content, it emits the two literal characters backslash+n
    (the whole file collapses onto one line, e.g. 'import pytest\\n\\ndef test_x():...') --
    reliably a SyntaxError for source files, and the model doesn't self-correct within the
    turn budget (see loc-25 20260814T095256Z run, write-tests-for-untested-module: 0/3, all
    three samples hit stuck_repeating on this exact pattern).

    Only fires when content has ZERO real newlines but several literal \\n -- a normally
    formatted file mixes real line breaks with the occasional intentional \\n inside a
    string literal, so that case is always left untouched. ponytail: blanket
    replace-everywhere, not a per-language parser that would spare a literal \\n legitimately
    embedded in a one-line file -- that input was already a guaranteed SyntaxError before this
    ran, so this can only make it better or leave it equally broken, never regress a case that
    would otherwise have worked. Upgrade to a real parser only if that rarer case is ever seen.
    """
    if "\n" not in content and content.count("\\n") >= 2:
        return content.replace("\\n", "\n").replace("\\t", "\t")
    return content


def tool_write_file(workdir: Path, args: dict) -> dict:
    path = resolve_safe_path(workdir, args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _unescape_if_flattened(args["content"])
    path.write_text(content, encoding="utf-8")
    result = {"status": "ok", "path": str(path.relative_to(workdir.resolve())), "bytes_written": len(content)}
    if content != args["content"]:
        result["note"] = (
            "your content had zero real newlines but multiple literal backslash-n sequences -- "
            "looks like escaped line breaks meant to be actual newlines, so the harness converted "
            "them before writing. Send real newlines directly next time, not escaped ones."
        )
    return result


def tool_read_file(workdir: Path, args: dict) -> dict:
    path = resolve_safe_path(workdir, args["path"])
    if not path.is_file():
        return {"status": "error", "message": f"no such file: {args['path']}"}
    content = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > MAX_READ_CHARS
    return {
        "status": "ok",
        "content": content[:MAX_READ_CHARS],
        "truncated": truncated,
    }


def tool_list_dir(workdir: Path, args: dict) -> dict:
    path = resolve_safe_path(workdir, args.get("path", "."))
    if not path.is_dir():
        return {"status": "error", "message": f"no such directory: {args.get('path', '.')}"}
    entries = []
    for p in sorted(path.iterdir()):
        entries.append({"name": p.name, "type": "dir" if p.is_dir() else "file"})
    return {"status": "ok", "entries": entries}


MAX_COMMAND_OUTPUT_CHARS = 4000

# Irreversible operations run_command refuses to execute directly. Not a security
# sandbox (still just string matching, not a real interpreter) -- a backstop against
# a cooperative model reaching for `python3 -c "shutil.rmtree(...)"` etc. once the
# obvious `rm`-style binary isn't in --allowed-commands. See PENDING_ACTION_FILENAME.
DESTRUCTIVE_PATTERNS = [
    re.compile(r"\bshutil\.rmtree\s*\("),
    re.compile(r"\bos\.remove\s*\("),
    re.compile(r"\bos\.unlink\s*\("),
    re.compile(r"\bos\.rmdir\s*\("),
    re.compile(r"\.unlink\s*\("),
    re.compile(r"\.rmdir\s*\("),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f"),
    re.compile(r"\bgit\s+push\s+(--force\b|-f\b)"),
    re.compile(r"\bgit\s+branch\s+-D\b"),
]

PENDING_ACTION_FILENAME = "pending-action.json"
PENDING_ACTION_TIMEOUT_HOURS = 48


def destructive_reason(command: str) -> str | None:
    """Returns a human-readable reason if `command` matches a known irreversible
    pattern, else None."""
    for pattern in DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return f"matches destructive pattern: {pattern.pattern}"
    return None


def execute_command(workdir: Path, command: str, allowed_bins: list, timeout: int, force: bool = False) -> dict:
    # ponytail: allowlist on the first token only, no OS-level sandbox (no network
    # block, no filesystem isolation beyond cwd) — good enough for a fixed set of
    # trusted local binaries (test runners, bd). Upgrade to sandbox-exec/containers
    # if this ever needs to run untrusted or network-touching commands.
    try:
        parts = shlex.split(command)
    except ValueError as e:
        return {"status": "error", "message": f"could not parse command: {e}"}
    if not parts:
        return {"status": "error", "message": "empty command"}
    if parts[0] not in allowed_bins:
        # ponytail: observed repeatedly (5+ times across real runs) — the model tries to
        # invoke a .py script directly by path, as if it were executable, instead of
        # prefixing "python3". Harmless and it always self-corrects the very next turn,
        # but that's a wasted turn every time. Auto-prepend rather than reject outright,
        # since the fix is unambiguous whenever python3 is itself allowed.
        if parts[0].endswith(".py") and "python3" in allowed_bins:
            parts = ["python3"] + parts
        else:
            return {"status": "error", "message": f"'{parts[0]}' is not in the allowed command list: {allowed_bins}"}
    if not force:
        reason = destructive_reason(command)
        if reason is not None:
            token = secrets.token_hex(4)
            pending = {
                "command": command,
                "reason": reason,
                "token": token,
                "proposed_at": datetime.now(timezone.utc).isoformat(),
            }
            (workdir / PENDING_ACTION_FILENAME).write_text(json.dumps(pending, indent=2), encoding="utf-8")
            return {
                "status": "pending_confirmation",
                "token": token,
                "message": (
                    f"This command was recognized as irreversible ({reason}) and was NOT executed. "
                    f"It has been recorded as a pending proposal (token {token}) for a human to approve "
                    "or reject out-of-band. Call finish() now, summarizing what you investigated and "
                    "what you're proposing -- do not try an alternate way to run this same operation."
                ),
            }
    try:
        proc = subprocess.run(
            parts, cwd=workdir, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"command timed out after {timeout}s"}
    return {
        "status": "ok",
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-MAX_COMMAND_OUTPUT_CHARS:],
        "stderr": proc.stderr[-MAX_COMMAND_OUTPUT_CHARS:],
    }


def resolve_pending_action(workdir: Path, token: str, allowed_bins: list, timeout: int) -> dict:
    """Called after out-of-band human approval (see PENDING_ACTION_FILENAME). Validates
    `token` against the pending proposal recorded in `workdir`, then -- if it matches and
    hasn't expired -- executes exactly that stored command, bypassing destructive_reason()
    this one time since it's now approved. The pending file is always cleared, whether the
    action ran or was refused, so a stale/expired token can't be reused."""
    pending_path = workdir / PENDING_ACTION_FILENAME
    if not pending_path.is_file():
        return {"status": "error", "message": "no pending action recorded for this workdir"}
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    if not secrets.compare_digest(token, pending.get("token", "")):
        return {"status": "error", "message": "token does not match the pending proposal"}
    proposed_at = datetime.fromisoformat(pending["proposed_at"])
    age_hours = (datetime.now(timezone.utc) - proposed_at).total_seconds() / 3600
    pending_path.unlink()
    if age_hours > PENDING_ACTION_TIMEOUT_HOURS:
        return {
            "status": "error",
            "message": (
                f"approval expired ({age_hours:.1f}h old, limit {PENDING_ACTION_TIMEOUT_HOURS}h) "
                "-- re-investigate and re-propose"
            ),
        }
    return execute_command(workdir, pending["command"], allowed_bins, timeout, force=True)


def tool_write_verdict(workdir: Path, args: dict) -> dict:
    # Always writes to a fixed filename — there is no path argument, by design, so
    # this can never be pointed at the file under review.
    path = workdir / "verdict.json"
    path.write_text(json.dumps({"pass": args["pass"], "note": args["note"]}, indent=2), encoding="utf-8")
    return {"status": "ok", "path": "verdict.json"}


def tool_web_search(workdir: Path, args: dict) -> dict:
    query = args.get("query", "")
    if not query:
        return {"status": "error", "message": "query is required"}
    if len(query) > MAX_WEB_SEARCH_QUERY_CHARS:
        return {
            "status": "error",
            "message": f"query is {len(query)} characters; the maximum is {MAX_WEB_SEARCH_QUERY_CHARS}",
        }
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        # Should already be caught by the pre-flight check_tavily_key_or_die() before the
        # run starts — this is a defensive fallback, not the primary guard.
        return {"status": "error", "message": "TAVILY_API_KEY not set in environment"}
    payload = json.dumps({"query": query, "max_results": MAX_SEARCH_RESULTS}).encode("utf-8")
    req = urllib.request.Request(
        TAVILY_SEARCH_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=WEB_REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"status": "error", "message": f"search request failed: {type(e).__name__}: {e}"}
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": (r.get("content") or "")[:500]}
        for r in body.get("results", [])[:MAX_SEARCH_RESULTS]
    ]
    if not results:
        return {"status": "error", "message": "search returned no results"}
    return {"status": "ok", "results": results}


def _response_charset(content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", content_type, re.IGNORECASE)
    return match.group(1).strip('"\'') if match else "utf-8"


def cache_fetched_page(
    cache_dir: Path, url: str, raw: bytes, content_type: str, text: str, truncated: bool,
) -> dict:
    """Create an immutable, session-local snapshot of a successful web fetch."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fetch_id = f"fetch-{timestamp}-{secrets.token_hex(3)}"
    snapshot_dir = cache_dir / fetch_id
    snapshot_dir.mkdir()
    (snapshot_dir / "raw.bin").write_bytes(raw)
    (snapshot_dir / "text.txt").write_text(text, encoding="utf-8")
    metadata = {
        "fetch_id": fetch_id,
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_type": content_type,
        "charset": _response_charset(content_type),
        "raw_bytes": len(raw),
        "text_chars": len(text),
        "truncated": truncated,
    }
    (snapshot_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def resolve_cached_fetch(cache_dir: Path, url: str, fetch_id: str = None) -> dict:
    """Look up a requested snapshot, defaulting to the latest matching URL."""
    if not cache_dir.is_dir():
        raise FileNotFoundError("no fetched pages are cached for this session; call web_fetch first")
    candidates = []
    if fetch_id:
        paths = [cache_dir / fetch_id]
    else:
        paths = sorted(cache_dir.glob("fetch-*"), reverse=True)
    for snapshot_dir in paths:
        metadata_path = snapshot_dir / "metadata.json"
        if not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if metadata.get("url") == url:
            metadata["snapshot_dir"] = snapshot_dir
            candidates.append(metadata)
    if not candidates:
        qualifier = f" with fetch_id '{fetch_id}'" if fetch_id else ""
        raise FileNotFoundError(f"no cached fetch for URL '{url}'{qualifier}; call web_fetch first")
    return candidates[0]


def save_fetched_page(workdir: Path, cache_dir: Path, args: dict) -> dict:
    format_name = args.get("format")
    if format_name not in {"raw", "text", "both"}:
        return {"status": "error", "message": "format must be raw, text, or both"}
    cached = resolve_cached_fetch(cache_dir, args.get("url", ""), args.get("fetch_id"))
    snapshot_dir = cached["snapshot_dir"]
    raw = (snapshot_dir / "raw.bin").read_bytes()
    text = (snapshot_dir / "text.txt").read_text(encoding="utf-8")
    requested_path = args.get("path", "")
    if not requested_path:
        return {"status": "error", "message": "path is required"}

    outputs = []
    if format_name == "both":
        raw_path = resolve_safe_path(workdir, f"{requested_path}.raw")
        text_path = resolve_safe_path(workdir, f"{requested_path}.txt")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        text_path.write_text(text, encoding="utf-8")
        outputs = [
            {"path": str(raw_path.relative_to(workdir.resolve())), "bytes_written": len(raw)},
            {"path": str(text_path.relative_to(workdir.resolve())), "chars_written": len(text)},
        ]
    elif format_name == "raw":
        output_path = resolve_safe_path(workdir, requested_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(raw)
        outputs = [{"path": str(output_path.relative_to(workdir.resolve())), "bytes_written": len(raw)}]
    else:
        output_path = resolve_safe_path(workdir, requested_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        outputs = [{"path": str(output_path.relative_to(workdir.resolve())), "chars_written": len(text)}]
    return {"status": "ok", "fetch_id": cached["fetch_id"], "outputs": outputs, "truncated": cached["truncated"]}


def print_fetched_page(cache_dir: Path, args: dict, output=None) -> dict:
    format_name = args.get("format")
    if format_name not in {"raw", "text"}:
        return {"status": "error", "message": "format must be raw or text"}
    cached = resolve_cached_fetch(cache_dir, args.get("url", ""), args.get("fetch_id"))
    snapshot_dir = cached["snapshot_dir"]
    if format_name == "raw":
        content = (snapshot_dir / "raw.bin").read_bytes().decode(cached["charset"], errors="replace")
    else:
        content = (snapshot_dir / "text.txt").read_text(encoding="utf-8")
    stream = output if output is not None else sys.stdout
    stream.write(content)
    if content and not content.endswith("\n"):
        stream.write("\n")
    stream.flush()
    return {
        "status": "ok", "fetch_id": cached["fetch_id"], "format": format_name,
        "chars_printed": len(content), "truncated": cached["truncated"],
    }


def tool_web_fetch(workdir: Path, args: dict, cache_dir: Path = None) -> dict:
    url = args.get("url", "")
    if not url:
        return {"status": "error", "message": "url is required"}
    if not url.startswith(("http://", "https://")):
        return {"status": "error", "message": "url must start with http:// or https://"}
    req = urllib.request.Request(url, headers={"User-Agent": "qwen-harness-web-fetch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=WEB_REQUEST_TIMEOUT) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if not any(t in content_type for t in ("text/html", "text/plain", "application/json")):
                return {"status": "error", "message": f"unsupported content-type: {content_type!r}"}
            raw = resp.read(MAX_FETCH_BYTES + 1)
    except Exception as e:
        return {"status": "error", "message": f"fetch failed: {type(e).__name__}: {e}"}
    truncated_bytes = len(raw) > MAX_FETCH_BYTES
    raw = raw[:MAX_FETCH_BYTES]
    text = raw.decode(_response_charset(content_type), errors="replace")
    if "html" in content_type:
        text = html_to_text(text)
    text_truncated_for_model = len(text) > MAX_FETCH_CHARS
    truncated = truncated_bytes
    result = {
        "status": "ok", "url": url, "text": text[:MAX_FETCH_CHARS],
        "truncated": truncated or text_truncated_for_model,
        "full_text_chars": len(text), "raw_bytes": len(raw),
    }
    if cache_dir is not None:
        cached = cache_fetched_page(cache_dir, url, raw, content_type, text, truncated)
        result["fetch_id"] = cached["fetch_id"]
        result["cached"] = True
        result["cache_truncated"] = truncated
    return result


DISPATCH = {
    "write_file": tool_write_file,
    "read_file": tool_read_file,
    "list_dir": tool_list_dir,
    "write_verdict": tool_write_verdict,
    "web_search": tool_web_search,
    "web_fetch": tool_web_fetch,
}


def derive_log_basename(task_path: str, log_prefix: str = None) -> str:
    """"task.txt" -> "harness-log" (unchanged default), "task-b.txt" -> "harness-log-b".
    Explicit log_prefix always wins. Exists so multiple variants sharing one flat
    --workdir get distinct log files instead of silently overwriting each other."""
    if log_prefix:
        return log_prefix
    task_stem = Path(task_path).stem
    suffix = task_stem[len("task"):] if task_stem.startswith("task") else f"-{task_stem}"
    return f"harness-log{suffix}"


def call_model(base_url: str, model: str, messages: list, temperature: float, max_tokens: int, tools: list) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # LM Studio's error body names the actual cause (e.g. context-length
        # exceeded, unknown model) — surface it instead of the bare "HTTP Error
        # 400", which told nothing about why the request was rejected.
        body = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body)["error"]["message"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        raise RuntimeError(f"HTTP {e.code} from {base_url}/chat/completions: {body}") from None


def build_chat_tools(selected_names: list[str], allowed_bins: list[str]) -> list:
    """Build the exact tool schema advertised for one interactive session.

    ``finish`` is deliberately always present: it ends one agent response, not the
    terminal session. All other schemas are opt-in so their definitions do not
    consume the model's context unless the user selected them during setup.
    """
    selected = set(selected_names)
    unknown = selected - set(CHAT_TOOL_NAMES)
    if unknown:
        raise ValueError(f"unknown chat tools: {', '.join(sorted(unknown))}")
    # A cache snapshot can only be created by web_fetch. Apply this here as
    # well as during interactive setup so resumed session configurations cannot
    # advertise cache-only tools that are impossible to use.
    if {"save_fetched_page", "print_fetched_page"} & selected:
        selected.add("web_fetch")
    if "run_command" in selected and ({"web_search", "web_fetch"} & selected):
        raise ValueError("run_command cannot be combined with web_search or web_fetch")

    tools = [tool for tool in TOOLS if tool["function"]["name"] in selected | {"finish"}]
    if "run_command" in selected:
        tools.append(make_run_command_tool(allowed_bins))
    if "web_search" in selected:
        tools.append(WEB_SEARCH_TOOL)
    if "web_fetch" in selected:
        tools.append(WEB_FETCH_TOOL)
    if "save_fetched_page" in selected:
        tools.append(SAVE_FETCHED_PAGE_TOOL)
    if "print_fetched_page" in selected:
        tools.append(PRINT_FETCHED_PAGE_TOOL)
    return tools


def _estimate_tokens(message: dict) -> int:
    """Rough ~4-chars-per-token estimate. No tokenizer dependency -- this is a soft
    safety budget for trim_chat_messages, not exact accounting."""
    chars = len(message.get("content") or "")
    for call in message.get("tool_calls") or []:
        chars += len(call.get("function", {}).get("arguments") or "")
    return chars // 4


def _group_tokens(group: list[dict]) -> int:
    return sum(_estimate_tokens(m) for m in group)


def trim_chat_messages(messages: list[dict], max_messages: int, max_tokens: int = None) -> list[dict]:
    """Return a rolling context without leaving tool calls orphaned.

    A model tool-call and its ``tool`` result must travel together. Therefore the
    unit of trimming is a user turn (one user message plus all subsequent
    assistant/tool messages), rather than blindly slicing the raw protocol list.
    A single tool-heavy turn may be larger than the soft limit.

    ``max_tokens``, if given, is a second, independent budget checked alongside the
    message-count window -- message count alone doesn't catch one large read_file
    result blowing the budget while still counting as a single message.
    """
    if max_messages <= 0:
        return []
    groups = []
    current = []
    for message in messages:
        if message.get("role") == "user":
            if current:
                groups.append(current)
            current = [message]
        elif current:
            current.append(message)
    if current:
        groups.append(current)
    kept = []
    kept_tokens = 0
    for group in reversed(groups):
        # A current tool-heavy turn can exceed the whole window by itself. Keep
        # its user request and only the most recent complete tool exchange(s),
        # rather than sending every previous failed attempt back to the model.
        if not kept and len(group) > max_messages:
            user_message, *responses = group
            exchanges = []
            current_exchange = []
            for message in responses:
                if message.get("role") == "assistant":
                    if current_exchange:
                        exchanges.append(current_exchange)
                    current_exchange = [message]
                elif current_exchange:
                    current_exchange.append(message)
            if current_exchange:
                exchanges.append(current_exchange)
            kept_responses = []
            kept_responses_tokens = 0
            budget = max_tokens - _estimate_tokens(user_message) if max_tokens is not None else None
            for exchange in reversed(exchanges):
                exchange_tokens = _group_tokens(exchange)
                if kept_responses and (
                    1 + len(exchange) + len(kept_responses) > max_messages
                    or (budget is not None and kept_responses_tokens + exchange_tokens > budget)
                ):
                    break
                kept_responses = exchange + kept_responses
                kept_responses_tokens += exchange_tokens
            return [user_message, *kept_responses]
        group_tokens = _group_tokens(group)
        if kept and (
            len(group) + len(kept) > max_messages
            or (max_tokens is not None and kept_tokens + group_tokens > max_tokens)
        ):
            break
        kept = group + kept
        kept_tokens += group_tokens
    return kept


def _chat_session_slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-_").lower()
    if not slug:
        raise ValueError("session name must contain letters or numbers")
    return slug


def _chat_history_markdown(messages: list[dict]) -> str:
    lines = ["# Chat history", ""]
    for message in messages:
        role = message.get("role", "unknown")
        if role == "tool":
            lines.extend(["## Tool result", "```json", message.get("content", ""), "```", ""])
            continue
        heading = "You" if role == "user" else "Agent"
        lines.extend([f"## {heading}", message.get("content") or ""])
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            lines.append(f"- Tool: `{function.get('name', 'unknown')}({function.get('arguments', '')})`")
        lines.append("")
    return "\n".join(lines)


def save_chat_session(session: dict) -> None:
    """Atomically persist a chat session and its human-readable transcript."""
    path = Path(session["path"])
    path.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    serializable = {key: value for key, value in session.items() if key != "path"}
    temp_path = path / "session.json.tmp"
    temp_path.write_text(json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path / "session.json")
    (path / "history.md").write_text(_chat_history_markdown(session.get("messages", [])), encoding="utf-8")


def load_chat_session(path: Path | str) -> dict:
    path = Path(path)
    data = json.loads((path / "session.json").read_text(encoding="utf-8"))
    if data.get("version") != CHAT_SESSION_VERSION:
        raise ValueError(f"unsupported chat session version: {data.get('version')!r}")
    if not isinstance(data.get("config"), dict) or not isinstance(data.get("messages"), list):
        raise ValueError("invalid chat session: expected config and messages")
    data["path"] = str(path)
    return data


def create_chat_session(
    sessions_dir: Path | str, name: str, model: str, base_url: str, workdir: Path | str,
    system_prompt: str, selected_tools: list[str], allowed_bins: list[str], max_turns: int,
    context_messages: int, context_tokens: int = DEFAULT_CHAT_CONTEXT_TOKENS,
) -> dict:
    sessions_dir = Path(sessions_dir)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    slug = _chat_session_slug(name)
    path = sessions_dir / slug
    suffix = 2
    while path.exists():
        path = sessions_dir / f"{slug}-{suffix}"
        suffix += 1
    workdir = Path(workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    session = {
        "version": CHAT_SESSION_VERSION,
        "name": name.strip(),
        "created_at": now,
        "updated_at": now,
        "context_start": 0,
        "config": {
            "model": model,
            "base_url": base_url.rstrip("/"),
            "workdir": str(workdir),
            "system_prompt": system_prompt,
            "selected_tools": list(selected_tools),
            "allowed_bins": list(allowed_bins),
            "max_turns": max_turns,
            "context_messages": context_messages,
            "context_tokens": context_tokens,
            "temperature": 0.4,
            "max_tokens": 8000,
            "command_timeout": 60,
        },
        "messages": [],
        "path": str(path),
    }
    save_chat_session(session)
    return session


def list_chat_sessions(sessions_dir: Path | str) -> list[dict]:
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.exists():
        return []
    sessions = []
    for metadata in sessions_dir.glob("*/session.json"):
        try:
            sessions.append(load_chat_session(metadata.parent))
        except (OSError, ValueError, json.JSONDecodeError):
            print(f"Skipping invalid session: {metadata.parent}", file=sys.stderr)
    return sorted(sessions, key=lambda session: session.get("updated_at", ""), reverse=True)


def fetch_available_models(base_url: str) -> list[str]:
    """Read model ids from the standard OpenAI-compatible ``/models`` endpoint."""
    request = urllib.request.Request(f"{base_url.rstrip('/')}/models", method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    models = [item.get("id") for item in data.get("data", []) if isinstance(item, dict) and item.get("id")]
    return sorted(models)


_CONTENT_CODE_FENCE_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*\r?\n(.*?)```", re.DOTALL)
_CONTENT_FENCE_PATH_COMMENT_RE = re.compile(r"^\s*#\s*([\w./-]+\.[A-Za-z0-9_]+)\s*$")
_CONTENT_INLINE_PATH_RE = re.compile(r"`([\w./-]+\.[A-Za-z0-9_]+)`")


def _content_fence_write_file_calls(content: str) -> list[dict]:
    """Albatross-style fallback: a model that fake-edits by printing a fenced code
    block (with the target path as its own first-line comment, or named in the prose
    immediately before the fence) instead of calling ``write_file``. Only fires when
    every fence in the message resolves to a path — a mix of "some fences look like
    file writes, others don't" is more likely prose showing example snippets than a
    genuine batch of edits, so it's left alone rather than guessed at.

    # ponytail: all-or-nothing per message, not per fence — a real edit sharing a
    # message with one unrelated example snippet synthesizes nothing at all and
    # falls through to no_tool_prose. Upgrade to per-fence resolution if that
    # mixed-content shape shows up in real transcripts.
    """
    fences = list(_CONTENT_CODE_FENCE_RE.finditer(content))
    if not fences:
        return []
    calls = []
    for index, match in enumerate(fences, start=1):
        body = match.group(1)
        if not body.strip():
            return []
        first_line = body.splitlines()[0]
        path_match = _CONTENT_FENCE_PATH_COMMENT_RE.match(first_line)
        if path_match:
            path = path_match.group(1)
            # Drop the marker line itself — it names the destination, it isn't part
            # of the file's intended content (and could break non-comment syntax).
            body = body[len(first_line):].lstrip("\r\n")
            if not body.strip():
                return []
        else:
            preceding = content[max(0, match.start() - 200):match.start()]
            inline_matches = _CONTENT_INLINE_PATH_RE.findall(preceding)
            if not inline_matches:
                return []
            path = inline_matches[-1]
        calls.append({
            "type": "function", "id": f"content-tool-{index}",
            "function": {
                "name": "write_file",
                "arguments": json.dumps({"path": path, "content": body}, ensure_ascii=False),
            },
        })
    return calls


def normalize_content_tool_calls(content: str, allowed_tool_names: set[str]) -> list[dict]:
    """Synthesize real tool calls out of a model's fake-edit prose response.

    Two dialects, tried in order: (1) the strict JSON tool-call array some local
    models emit instead of a native tool call — a non-empty JSON array whose items
    have exactly the ``name``/``arguments``/optional-``id`` shape, with every name
    enabled in this session; (2) markdown-fenced code blocks with a resolvable target
    path (see `_content_fence_write_file_calls`), the shape actually observed in
    repo-context eval failures (prose description + ```python fence, never a real
    write_file call). Any other text remains user-facing assistant content and is
    never executed.
    """
    if not isinstance(content, str):
        return []
    try:
        items = json.loads(content)
    except json.JSONDecodeError:
        items = None
    if isinstance(items, list) and items:
        calls = []
        allowed_keys = {"name", "arguments", "id"}
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict) or set(item) - allowed_keys:
                return []
            name = item.get("name")
            arguments = item.get("arguments")
            if not isinstance(name, str) or name not in allowed_tool_names or not isinstance(arguments, dict):
                return []
            try:
                encoded_arguments = json.dumps(arguments, ensure_ascii=False)
            except (TypeError, ValueError):
                return []
            call_id = item.get("id")
            if call_id is not None and not isinstance(call_id, str):
                return []
            calls.append({
                "type": "function", "id": call_id or f"content-tool-{index}",
                "function": {"name": name, "arguments": encoded_arguments},
            })
        return calls
    if "write_file" not in allowed_tool_names:
        return []
    return _content_fence_write_file_calls(content)


def run_chat_turn(
    messages: list[dict], system_prompt: str, workdir: Path, tools: list, allowed_bins: list,
    model: str, base_url: str, temperature: float, max_tokens: int, max_turns: int,
    command_timeout: int, context_messages: int, context_tokens: int = None, fetch_cache_dir: Path = None,
) -> dict:
    """Run one interactive user request and return the updated complete transcript."""
    transcript = list(messages)
    current_user_urls = set()
    for message in reversed(transcript):
        if message.get("role") == "user":
            current_user_urls = extract_urls(message.get("content", ""))
            break
    unfetched_user_urls = set(current_user_urls)
    successful_web_sources = 0
    web_browsing_closed_reason = None
    policy_system_prompt = f"{system_prompt}\n\n{CHAT_WEB_POLICY_PROMPT}"

    def build_context(recovery_instruction: str = None) -> list[dict]:
        prompt = policy_system_prompt
        if recovery_instruction:
            prompt += f"\n\n{recovery_instruction}"
        return [{"role": "system", "content": prompt}] + trim_chat_messages(transcript, context_messages, context_tokens)

    context = build_context()
    allowed_tool_names = {tool["function"]["name"] for tool in tools}
    events = []
    summary = None
    crashed = None
    finished = False
    last_tool_signature = None
    consecutive_tool_calls = 0
    web_requests = 0
    failed_web_attempts_without_source = 0
    truncation_retries = 0
    loop_recovery_reason = None
    final_recovery_reason = None
    recovery_kind = None
    loop_recovery_failed = False
    final_recovery_failed = False
    normalized_content_tool_calls = 0

    def loop_recovery_evidence() -> str:
        """Compact successful tool material for the final no-tool recovery turn."""
        parts = []
        remaining = LOOP_RECOVERY_EVIDENCE_CHARS
        for event in events:
            result = event.get("result", {})
            if result.get("status") != "ok":
                continue
            text = json.dumps({"tool": event["tool"], "result": result}, ensure_ascii=False)
            if len(text) > remaining:
                text = text[:remaining] + "…"
            parts.append(text)
            remaining -= len(text)
            if remaining <= 0:
                break
        return "\n".join(parts) or "No successful tool result was available."
    try:
        for turn in range(1, max_turns + 1):
            body = call_model(base_url, model, context, temperature, max_tokens, tools)
            choice = body["choices"][0]
            raw_message = choice["message"]
            tool_calls = raw_message.get("tool_calls") or []
            content = raw_message.get("content") or ""
            if not tool_calls:
                normalized_calls = normalize_content_tool_calls(content, allowed_tool_names)
                if normalized_calls:
                    tool_calls = normalized_calls
                    normalized_content_tool_calls += len(normalized_calls)
                    content = ""
            assistant_message = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls

            if not tool_calls:
                if choice.get("finish_reason") == "length":
                    if truncation_retries == 0:
                        truncation_retries += 1
                        context = build_context(
                            "RECOVERY: Your previous response was cut off while forming a tool call. "
                            "Call web_search with exactly one concise query of at most 300 characters, "
                            "or answer without tools. Do not repeat words or emit a long JSON string."
                        )
                        continue
                    crashed = (
                        f"truncated at max_tokens={max_tokens} again after one recovery retry; "
                        "the model could not produce a valid response or tool call"
                    )
                else:
                    summary = assistant_message["content"] or "(empty response)"
                    finished = True
                transcript.append(assistant_message)
                break

            transcript.append(assistant_message)
            context.append(assistant_message)

            for call in tool_calls:
                fn_name = call.get("function", {}).get("name", "")
                network_request_executed = False
                blocked_by_web_limit = False
                try:
                    fn_args = json.loads(call.get("function", {}).get("arguments") or "{}")
                except json.JSONDecodeError as exc:
                    fn_args = {}
                    result = {"status": "error", "message": f"bad JSON arguments: {exc}"}
                else:
                    if fn_name not in allowed_tool_names:
                        result = {"status": "error", "message": f"tool '{fn_name}' is not available in this session"}
                    elif fn_name in {"web_search", "web_fetch"} and web_browsing_closed_reason:
                        result = {
                            "status": "error",
                            "message": f"web browsing is closed: {web_browsing_closed_reason} Call finish with the available material.",
                        }
                    elif fn_name == "web_search" and unfetched_user_urls:
                        urls = ", ".join(sorted(unfetched_user_urls))
                        result = {
                            "status": "error",
                            "message": (
                                "web_search is blocked because the user supplied URL(s) have not been "
                                f"fetched yet: {urls}. Call web_fetch for each URL first."
                            ),
                        }
                    elif fn_name in {"web_search", "web_fetch"} and web_requests >= MAX_WEB_REQUESTS_PER_CHAT_TURN:
                        blocked_by_web_limit = True
                        web_browsing_closed_reason = (
                            f"the limit of {MAX_WEB_REQUESTS_PER_CHAT_TURN} web requests for this user message was reached"
                        )
                        final_recovery_reason = web_browsing_closed_reason
                        result = {
                            "status": "error",
                            "message": f"web browsing is closed: {web_browsing_closed_reason}. Call finish with the available material.",
                        }
                    elif fn_name == "finish":
                        summary = fn_args.get("summary", "")
                        finished = True
                        result = {"status": "ok"}
                    elif fn_name == "run_command":
                        try:
                            result = execute_command(workdir, fn_args.get("command", ""), allowed_bins, command_timeout)
                        except Exception as exc:
                            result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
                    elif fn_name == "web_fetch":
                        web_requests += 1
                        network_request_executed = True
                        try:
                            result = (
                                tool_web_fetch(workdir, fn_args, fetch_cache_dir)
                                if fetch_cache_dir is not None else DISPATCH["web_fetch"](workdir, fn_args)
                            )
                        except Exception as exc:
                            result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
                    elif fn_name == "web_search":
                        web_requests += 1
                        network_request_executed = True
                        try:
                            result = DISPATCH["web_search"](workdir, fn_args)
                        except Exception as exc:
                            result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
                    elif fn_name == "save_fetched_page":
                        if fetch_cache_dir is None:
                            result = {"status": "error", "message": "page cache is not enabled in this session"}
                        else:
                            try:
                                result = save_fetched_page(workdir, fetch_cache_dir, fn_args)
                            except Exception as exc:
                                result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
                    elif fn_name == "print_fetched_page":
                        if fetch_cache_dir is None:
                            result = {"status": "error", "message": "page cache is not enabled in this session"}
                        else:
                            try:
                                result = print_fetched_page(fetch_cache_dir, fn_args)
                            except Exception as exc:
                                result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
                    elif fn_name in DISPATCH:
                        try:
                            result = DISPATCH[fn_name](workdir, fn_args)
                        except Exception as exc:
                            result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
                    else:
                        result = {"status": "error", "message": f"unknown tool: {fn_name}"}

                if fn_name == "web_fetch" and not blocked_by_web_limit:
                    fetched_url = fn_args.get("url", "").rstrip(_URL_TRAILING_PUNCT)
                    # A failed fetch still satisfies the ordering policy: the model
                    # can now search for an alternative source instead of retrying
                    # the same inaccessible URL forever.
                    unfetched_user_urls.discard(fetched_url)
                    if (
                        fetched_url in current_user_urls
                        and result.get("status") == "ok"
                        and not unfetched_user_urls
                    ):
                        web_browsing_closed_reason = (
                            "the user-supplied URL was fetched successfully; no additional web source is needed"
                        )
                if network_request_executed and web_requests >= MAX_WEB_REQUESTS_PER_CHAT_TURN:
                    web_browsing_closed_reason = (
                        f"the limit of {MAX_WEB_REQUESTS_PER_CHAT_TURN} web requests for this user message was reached"
                    )
                    final_recovery_reason = web_browsing_closed_reason
                if blocked_by_web_limit:
                    pass
                elif fn_name == "web_fetch" and result.get("status") == "ok":
                    successful_web_sources += 1
                elif fn_name == "web_search" and result.get("status") == "ok" and result.get("results"):
                    successful_web_sources += 1
                elif (
                    fn_name in {"web_search", "web_fetch"}
                    and result.get("status") == "error"
                    and successful_web_sources > 0
                    and not web_browsing_closed_reason
                ):
                    web_browsing_closed_reason = (
                        f"{fn_name} failed after {successful_web_sources} existing successful web source(s)"
                    )
                    result["message"] = (
                        f"{result.get('message', 'web operation failed')}. A web operation failed, but there "
                        f"is {successful_web_sources} existing successful web source(s). Stop browsing and "
                        "call finish with the best answer supported by that material; state any limitation."
                    )
                elif fn_name in {"web_search", "web_fetch"} and result.get("status") == "error" and not web_browsing_closed_reason:
                    failed_web_attempts_without_source += 1
                    if failed_web_attempts_without_source >= 2:
                        web_browsing_closed_reason = (
                            "a web request failed and its one permitted alternative attempt also failed"
                        )
                        result["message"] = (
                            f"{result.get('message', 'web operation failed')}. No successful web source was obtained. "
                            "Stop browsing and call finish with a clear explanation that the material could not be retrieved."
                        )
                    else:
                        result["message"] = (
                            f"{result.get('message', 'web operation failed')}. No successful web source exists yet. "
                            "You may make one alternative web request, then answer honestly if it also fails."
                        )

                events.append({
                    "tool": fn_name, "result": result,
                    "network_request_executed": network_request_executed,
                })
                tool_message = {
                    "role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(result),
                }
                transcript.append(tool_message)
                context.append(tool_message)
                signature = (fn_name, json.dumps(fn_args, sort_keys=True, ensure_ascii=False))
                consecutive_tool_calls = (
                    consecutive_tool_calls + 1 if signature == last_tool_signature else 1
                )
                last_tool_signature = signature
                if consecutive_tool_calls >= REPEATED_CHAT_TOOL_CALL_THRESHOLD:
                    loop_recovery_reason = (
                        f"repeated tool call '{fn_name}' {consecutive_tool_calls} times with identical "
                        "arguments"
                    )
                    break
            if crashed or loop_recovery_reason or final_recovery_reason:
                break
            if finished:
                break
            # Rebuild the request from the rolling transcript on every tool
            # iteration. Appending forever here was why one stuck web fetch
            # grew a 30-message chat context into an 80-message prompt.
            context = build_context()
        else:
            final_recovery_reason = f"the turn limit of {max_turns} was reached before the task completed"
    except Exception as exc:
        crashed = f"{type(exc).__name__}: {exc}"

    recovery_reason = loop_recovery_reason or final_recovery_reason
    if recovery_reason and not crashed and not finished:
        if loop_recovery_reason:
            recovery_kind = "loop"
        elif web_requests >= MAX_WEB_REQUESTS_PER_CHAT_TURN:
            recovery_kind = "web_limit"
        else:
            recovery_kind = "turn_limit"
        instruction = (
            f"FINAL RECOVERY: {recovery_reason}. Tools are now disabled. "
            "Give the user a concise final answer using only the successful tool material below. "
            "Do not mention internal mechanics. If the material is insufficient, give the best partial answer and say "
            "exactly what could not be established. Preserve any URLs, dates, item counts, or file paths the user asked for. "
            "Do not call a tool or finish.\n\n"
            f"Collected material:\n{loop_recovery_evidence()}"
        )
        try:
            body = call_model(
                base_url, model, build_context(instruction), temperature, max_tokens, [],
            )
            raw_message = body["choices"][0]["message"]
            content = (raw_message.get("content") or "").strip()
            has_tool_call = bool(raw_message.get("tool_calls")) or bool(
                normalize_content_tool_calls(content, allowed_tool_names)
            )
            if content and not has_tool_call and "<tool_call" not in content.lower() and "</tool_call" not in content.lower():
                transcript.append({"role": "assistant", "content": content})
                summary = content
            else:
                final_recovery_failed = True
        except Exception as exc:
            final_recovery_failed = True
        if final_recovery_failed:
            loop_recovery_failed = recovery_kind == "loop"
            fallback_prefix = {
                "loop": "I could not complete the requested tool workflow after repeated identical calls.",
                "web_limit": "I reached the web-request limit before completing the requested workflow.",
                "turn_limit": "I reached the agent turn limit before completing the requested workflow.",
            }[recovery_kind]
            summary = (
                f"{fallback_prefix} Available material: " + loop_recovery_evidence()
            )
            transcript.append({"role": "assistant", "content": summary})
        finished = True

    return {
        "messages": transcript, "finished": finished, "summary": summary,
        "crashed": crashed, "events": events, "recovered_from_loop": recovery_kind == "loop",
        "loop_recovery_failed": loop_recovery_failed,
        "recovered_from_web_limit": recovery_kind == "web_limit",
        "recovered_from_turn_limit": recovery_kind == "turn_limit",
        "final_recovery_failed": final_recovery_failed,
        "normalized_content_tool_calls": normalized_content_tool_calls,
        "web_requests": web_requests,
    }


def _prompt_int(prompt: str, default: int, minimum: int = 1) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if value < minimum:
            print(f"Please enter a number of at least {minimum}.")
            continue
        return value


def _choose_chat_model(base_url: str) -> str:
    try:
        models = fetch_available_models(base_url)
    except Exception as exc:
        models = []
        print(f"Could not list models from {base_url}: {type(exc).__name__}: {exc}")

    if models:
        print("Available models:")
        for index, model in enumerate(models, start=1):
            print(f"  {index}. {model}")
        print("  m. Enter a model ID manually")
        while True:
            choice = input("Model: ").strip()
            if choice.lower() == "m":
                break
            try:
                index = int(choice)
                if not 1 <= index <= len(models):
                    raise ValueError
                return models[index - 1]
            except (ValueError, IndexError):
                print("Choose a listed number or m.")
    return input("Model ID: ").strip()


def _choose_chat_tools() -> tuple[list[str], list[str]]:
    options = [
        ("read_file", "read files"),
        ("write_file", "write files"),
        ("list_dir", "list directories"),
        ("run_command", "run approved commands"),
    ]
    if os.environ.get("TAVILY_API_KEY"):
        options.extend([
            ("web_search", "search the web"),
            ("web_fetch", "fetch web pages"),
            ("page_cache", "save or print previously fetched pages"),
        ])
    else:
        print("Web tools are unavailable because TAVILY_API_KEY is not set.")

    while True:
        print("Choose tools for this session (comma-separated numbers; blank = no optional tools):")
        for index, (_, label) in enumerate(options, start=1):
            print(f"  {index}. {label}")
        raw = input("Tools: ").strip()
        if not raw:
            selected = []
        else:
            try:
                positions = [int(item.strip()) for item in raw.split(",")]
                if any(position < 1 or position > len(options) for position in positions):
                    raise ValueError
                selected = [options[position - 1][0] for position in positions]
            except (ValueError, IndexError):
                print("Enter only numbers from the list, separated by commas.")
                continue
        selected = list(dict.fromkeys(selected))
        if "page_cache" in selected:
            selected.remove("page_cache")
            selected.extend(["web_fetch", "save_fetched_page", "print_fetched_page"])
        if "run_command" in selected and ({"web_search", "web_fetch"} & set(selected)):
            print("run_command cannot be combined with web tools. Choose again.")
            continue
        break

    allowed_bins = []
    if "run_command" in selected:
        default_bins = "pytest,python3,node,npm,bd"
        raw_bins = input(f"Allowed command binaries [{default_bins}]: ").strip() or default_bins
        allowed_bins = [item.strip() for item in raw_bins.split(",") if item.strip()]
    return selected, allowed_bins


def _create_chat_session_interactively(sessions_dir: Path, base_url: str) -> dict:
    print("\nNew chat session")
    while True:
        name = input("Session name: ").strip()
        try:
            _chat_session_slug(name)
            break
        except ValueError as exc:
            print(exc)
    model = _choose_chat_model(base_url)
    while not model:
        print("A model ID is required.")
        model = _choose_chat_model(base_url)
    default_workdir = str(Path.cwd())
    workdir = input(f"Working directory [{default_workdir}]: ").strip() or default_workdir
    while True:
        prompt_file = input("Custom system-prompt file (blank = built-in prompt): ").strip()
        if not prompt_file:
            system_prompt = DEFAULT_CHAT_SYSTEM_PROMPT
            break
        try:
            system_prompt = Path(prompt_file).expanduser().read_text(encoding="utf-8")
            break
        except OSError as exc:
            print(f"Could not read {prompt_file}: {exc}")
    selected_tools, allowed_bins = _choose_chat_tools()
    max_turns = _prompt_int("Maximum agent turns per message", DEFAULT_CHAT_MAX_TURNS)
    context_messages = _prompt_int("Rolling context message limit", DEFAULT_CHAT_CONTEXT_MESSAGES)
    context_tokens = _prompt_int("Rolling context token budget (rough estimate)", DEFAULT_CHAT_CONTEXT_TOKENS)
    return create_chat_session(
        sessions_dir=sessions_dir, name=name, model=model, base_url=base_url, workdir=workdir,
        system_prompt=system_prompt, selected_tools=selected_tools, allowed_bins=allowed_bins,
        max_turns=max_turns, context_messages=context_messages, context_tokens=context_tokens,
    )


def _print_chat_history(messages: list[dict]) -> None:
    for message in messages:
        role = message.get("role")
        if role == "user":
            print(f"you> {message.get('content', '')}")
        elif role == "assistant":
            if message.get("content"):
                print(f"agent> {message['content']}")
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                if function.get("name") == "finish":
                    try:
                        summary = json.loads(function.get("arguments") or "{}").get("summary", "")
                    except json.JSONDecodeError:
                        summary = ""
                    if summary:
                        print(f"agent> {summary}")


def run_chat_session(session: dict) -> None:
    """Own the terminal loop for one already-created or resumed session."""
    config = session["config"]
    selected_tools = config["selected_tools"]
    if ({"web_search", "web_fetch"} & set(selected_tools)) and not os.environ.get("TAVILY_API_KEY"):
        print("This session needs TAVILY_API_KEY for its selected web tools.", file=sys.stderr)
        return
    try:
        tools = build_chat_tools(selected_tools, config["allowed_bins"])
    except ValueError as exc:
        print(f"Invalid session tool configuration: {exc}", file=sys.stderr)
        return
    workdir = Path(config["workdir"])
    fetch_cache_dir = (
        Path(session["path"]) / "fetches"
        if {"save_fetched_page", "print_fetched_page"} & set(selected_tools) else None
    )
    workdir.mkdir(parents=True, exist_ok=True)
    print(
        f"\nSession: {session['name']} | model: {config['model']} | workdir: {workdir}\n"
        "Commands: /help, /history, /clear, /exit"
    )

    while True:
        try:
            user_input = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            save_chat_session(session)
            print("\nSession saved.")
            return
        if not user_input:
            continue
        if user_input == "/exit":
            save_chat_session(session)
            print("Session saved.")
            return
        if user_input == "/help":
            print("/help — show commands; /history — print full saved conversation; "
                  "/clear — start a fresh model context while preserving the archive; /exit — save and quit")
            continue
        if user_input == "/history":
            _print_chat_history(session["messages"])
            continue
        if user_input == "/clear":
            session["context_start"] = len(session["messages"])
            save_chat_session(session)
            print("The next request will start with only the system prompt; the full archive is still saved.")
            continue
        if user_input.startswith("/"):
            print("Unknown command. Type /help.")
            continue

        context_start = session.get("context_start", 0)
        prefix = session["messages"][:context_start]
        active_messages = session["messages"][context_start:]
        active_messages.append({"role": "user", "content": user_input})
        session["messages"] = prefix + active_messages
        save_chat_session(session)  # preserve the request even if the model/server fails
        print(f"agent ({config['model']}) is working...", file=sys.stderr)
        result = run_chat_turn(
            messages=active_messages, system_prompt=config["system_prompt"], workdir=workdir, tools=tools,
            allowed_bins=config["allowed_bins"], model=config["model"], base_url=config["base_url"],
            temperature=config.get("temperature", 0.4), max_tokens=config.get("max_tokens", 8000),
            max_turns=config["max_turns"], command_timeout=config.get("command_timeout", 60),
            context_messages=config["context_messages"],
            context_tokens=config.get("context_tokens", DEFAULT_CHAT_CONTEXT_TOKENS),
            fetch_cache_dir=fetch_cache_dir,
        )
        session["messages"] = prefix + result["messages"]
        save_chat_session(session)
        for event in result["events"]:
            status = event["result"].get("status", "ok")
            print(f"  [{event['tool']}: {status}]", file=sys.stderr)
            if status in ("error", "pending_confirmation"):
                print(f"  {event['result']}", file=sys.stderr)
        if result["crashed"]:
            print(f"Agent error: {result['crashed']}")
        else:
            print(f"agent> {result['summary'] or '(completed without a summary)'}")


def chat_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Interactive persistent chat for local OpenAI-compatible models.")
    parser.add_argument("--base-url", default="http://localhost:1234/v1")
    parser.add_argument(
        "--sessions-dir", default=str(Path.home() / ".qwen-harness" / "sessions"),
        help="Directory for saved sessions (default: ~/.qwen-harness/sessions)",
    )
    args = parser.parse_args(argv)
    sessions_dir = Path(args.sessions_dir).expanduser()
    sessions = list_chat_sessions(sessions_dir)
    while True:
        print("\nSaved sessions:")
        print("  0. Create a new session")
        for index, session in enumerate(sessions, start=1):
            config = session["config"]
            print(f"  {index}. {session['name']} — {config['model']} — updated {session['updated_at']}")
        choice = input("Choose a session number (or q to quit): ").strip().lower()
        if choice == "q":
            return
        try:
            selected = int(choice)
            if selected == 0:
                session = _create_chat_session_interactively(sessions_dir, args.base_url)
                break
            if selected < 0 or selected > len(sessions):
                raise ValueError
            session = sessions[selected - 1]
            break
        except (ValueError, IndexError):
            print("Choose a listed number or q.")
    run_chat_session(session)


def run_once(
    workdir: Path, system_prompt: str, task_prompt: str, tools: list, allowed_tool_names: set,
    allowed_bins: list, model: str, base_url: str, temperature: float, max_tokens: int,
    max_turns: int, command_timeout: int,
    verify_command: str, verify_timeout: int, log_basename: str, stderr_prefix: str = "",
    recovery_turns: int = 0, workspace_bootstrap: bool = False,
    workspace_inventory_limit: int = DEFAULT_WORKSPACE_INVENTORY_LIMIT,
    bootstrap_include_verify_command: bool = False,
    repeated_command_failure_threshold: int = DEFAULT_REPEATED_COMMAND_FAILURE_THRESHOLD,
) -> dict:
    """Run one full agent-loop attempt in `workdir`. Same logic main() used to run inline —
    extracted so --samples N can call this once per isolated sample workdir. Writes
    <log_basename>.json/.md into `workdir` same as a single-sample run always did.

    ``recovery_turns`` is opt-in and permits that many additional model turns only after
    the harness's initial verification fails.  The verifier remains the sole authority:
    recovery succeeds only if a later, harness-owned verification exits zero.

    Returns {"finished": bool, "crashed": str|None, "finish_summary": str|None, "turns_used": int,
    "unverified_urls": list[str]}. unverified_urls (only meaningful with --enable-web-access):
    URLs the model wrote into any write_file content but never actually passed to web_fetch —
    same "never trust the model's own claim, verify mechanically" principle as
    --verify-command, just informational rather than a hard failure (a URL can legitimately be
    cited from a search snippet without being fetched, so this doesn't override `finished`).
    """
    if repeated_command_failure_threshold < 1:
        raise ValueError("repeated command failure threshold must be at least 1")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_prompt},
    ]
    if workspace_bootstrap:
        messages.append({
            "role": "user",
            "content": build_workspace_bootstrap(
                workdir, workspace_inventory_limit,
                verify_command if bootstrap_include_verify_command else None,
            ),
        })
    log = []
    finished = False
    finish_summary = None
    crashed = None
    repeat_tracker = {}  # path -> (last_content_hash, consecutive_count)
    command_failure_counts = {}  # exact command -> consecutive failed invocations
    stuck_repeating = False
    stuck_command = None  # the exact command that hit repeated_command_failure_threshold
    fetched_urls = set()  # urls that actually went through a successful web_fetch call
    mentioned_urls = set()  # urls the model wrote into any write_file content
    total_model_turns = 0
    completion_kind = None
    initial_failure = None
    verification_results = []
    recovery_attempts = 0
    recovery_turn_limit_reached = False
    current_plan = ""  # model's latest update_plan call, re-shown to it every turn (see call_model call site)

    def write_logs():
        log_json = workdir / f"{log_basename}.json"
        log_json.write_text(
            json.dumps({
                "turns": log, "finished": finished, "summary": finish_summary, "crashed": crashed,
                "unverified_urls": sorted(mentioned_urls - fetched_urls),
                "initial_failure": initial_failure,
                "verification_results": verification_results,
                "final_plan": current_plan,
                "recovery": {
                    "enabled": recovery_turns > 0,
                    "turn_limit": recovery_turns,
                    "attempts": recovery_attempts,
                    "turn_limit_reached": recovery_turn_limit_reached,
                },
            }, indent=2),
            encoding="utf-8",
        )
        log_md_lines = [
            "# Harness run log", "",
            f"Model: `{model}`  ",
            f"Model turns used: {total_model_turns}/{max_turns + recovery_turns}  ",
            f"Finished cleanly: {finished}  ",
        ]
        if crashed:
            log_md_lines.append(f"Crashed: {crashed}  ")
        if initial_failure:
            log_md_lines += ["", "## Initial failure", f"Cause: `{initial_failure['cause']}`  "]
        if verification_results:
            log_md_lines += ["", "## Verification results"]
            for result in verification_results:
                outcome = "passed" if result["passed"] else "failed"
                log_md_lines.append(
                    f"- {result['phase']} verification after {result['completion_kind']}: **{outcome}**"
                )
        if recovery_attempts or recovery_turn_limit_reached:
            log_md_lines += ["", "## Recovery", f"Recovery model turns: {recovery_attempts}/{recovery_turns}  "]
            if recovery_turn_limit_reached:
                log_md_lines.append("Recovery turn limit reached before a subsequent completion.  ")
        log_md_lines.append("")
        for entry in log:
            if entry.get("kind") == "verification":
                log_md_lines.append(f"## Verification ({entry['phase']})")
                log_md_lines.append(f"Command: `{entry['verify_command']}`")
                log_md_lines.append(f"Result: `{entry['verify_result']}`")
                log_md_lines.append("")
                continue
            msg = entry["response"]
            log_md_lines.append(f"## {entry.get('phase', 'initial').title()} turn {entry['turn']}")
            if msg.get("content"):
                log_md_lines.append(msg["content"])
            for call in msg.get("tool_calls") or []:
                log_md_lines.append(f"- `{call['function']['name']}({call['function']['arguments']})`")
            log_md_lines.append("")
        if finish_summary:
            log_md_lines += ["## Summary", finish_summary]
        (workdir / f"{log_basename}.md").write_text("\n".join(log_md_lines), encoding="utf-8")

    def run_agent_phase(turn_limit: int, phase: str) -> bool:
        """Run one bounded model phase and return whether it reached a completion."""
        nonlocal finished, finish_summary, crashed, stuck_repeating, stuck_command, completion_kind
        nonlocal total_model_turns, recovery_attempts, current_plan
        for phase_turn in range(1, turn_limit + 1):
            total_model_turns += 1
            if phase == "recovery":
                recovery_attempts += 1
            print(
                f"{stderr_prefix}[{phase} turn {phase_turn}/{turn_limit}] calling {model}...",
                file=sys.stderr,
            )
            # current_plan is re-shown fresh each turn rather than stored in `messages`, so it
            # stays a single compact reminder instead of one more entry in a growing transcript.
            call_messages = messages
            if current_plan:
                call_messages = messages + [{
                    "role": "user",
                    "content": f"[Your current plan, from your last update_plan call]\n{current_plan}",
                }]
            body = call_model(base_url, model, call_messages, temperature, max_tokens, tools)
            choice = body["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason")
            log.append({
                "turn": phase_turn, "phase": phase, "response": message,
                "usage": body.get("usage"), "finish_reason": finish_reason,
            })

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                synthesized_calls = normalize_content_tool_calls(message.get("content", ""), allowed_tool_names)
                if synthesized_calls:
                    print(
                        f"{stderr_prefix}[{phase} turn {phase_turn}] synthesized "
                        f"{len(synthesized_calls)} tool call(s) from fake-edit prose content.",
                        file=sys.stderr,
                    )
                    tool_calls = message["tool_calls"] = synthesized_calls
                    message["content"] = ""
            if not tool_calls:
                if finish_reason == "length":
                    # Truncated mid-generation (e.g. burned the budget on reasoning_content)
                    # before ever emitting a tool call — this is a stall, not success. Don't
                    # let it masquerade as finished; fail loudly so it gets retried with more
                    # budget instead of silently producing no output.
                    crashed = f"truncated at max_tokens={max_tokens} before any tool call (finish_reason=length)"
                    print(f"{stderr_prefix}[{phase} turn {phase_turn}] {crashed}", file=sys.stderr)
                    return False
                # This remains a completion so the harness can mechanically check it. With
                # a verifier, it is recorded as an actionable no-tool attempt rather than an
                # unqualified success; a failing check can trigger opt-in recovery.
                content = message.get("content", "")
                print(
                    f"{stderr_prefix}[{phase} turn {phase_turn}] model stopped without calling finish(); "
                    "checking mechanically.",
                    file=sys.stderr,
                )
                finished = True
                finish_summary = content or "(no tool call, no content)"
                completion_kind = "no_tool_prose"
                return True

            # Assistant message must be echoed back before tool results.
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})

            for call in tool_calls:
                fn_name = call["function"]["name"]
                try:
                    fn_args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError as e:
                    result = {"status": "error", "message": f"bad JSON arguments: {e}"}
                    fn_args = {}
                else:
                    if fn_name not in allowed_tool_names:
                        result = {"status": "error", "message": f"tool '{fn_name}' is not available in this mode"}
                    elif fn_name == "finish":
                        result = {"status": "ok"}
                        finished = True
                        finish_summary = fn_args.get("summary", "")
                        completion_kind = "finish"
                    elif fn_name == "update_plan":
                        current_plan = fn_args.get("plan", "")
                        result = {"status": "ok"}
                    elif fn_name == "run_command":
                        try:
                            result = execute_command(workdir, fn_args.get("command", ""), allowed_bins, command_timeout)
                        except Exception as e:
                            result = {"status": "error", "message": f"{type(e).__name__}: {e}"}
                    elif fn_name == "write_file" and "path" in fn_args and _targets_verify_script(workdir, fn_args["path"], verify_command):
                        # Observed for real (Variant C, sample 4 of a --samples batch): a
                        # model faced with a persistently "broken" checker call wrote its
                        # own always-passing stub over the checker script instead of fixing
                        # its own path usage. The harness's OWN re-run of --verify-command
                        # already catches the downstream effect of that, but blocking the
                        # write itself is cheaper and stops it before it wastes more turns.
                        result = {
                            "status": "error",
                            "message": (
                                "refusing to write_file over the script referenced in "
                                "--verify-command — that script grades your work, you can't "
                                "edit it to make your own output pass. If a call to it is "
                                "failing with 'file not found', your PATH to it is wrong, "
                                "not the script itself; fix the path you're calling, not the "
                                "script."
                            ),
                        }
                    elif fn_name in DISPATCH:
                        try:
                            result = DISPATCH[fn_name](workdir, fn_args)
                        except Exception as e:
                            # Missing/malformed args, bad path, etc — feed the error back
                            # to the model as a tool result so it can retry, never crash
                            # the harness over a bad tool call.
                            result = {"status": "error", "message": f"{type(e).__name__}: {e}"}
                    else:
                        result = {"status": "error", "message": f"unknown tool: {fn_name}"}

                if fn_name == "write_file" and "content" in fn_args:
                    path_key = fn_args.get("path", "")
                    content_hash = hashlib.md5(fn_args["content"].encode("utf-8")).hexdigest()
                    last_hash, count = repeat_tracker.get(path_key, (None, 0))
                    count = count + 1 if content_hash == last_hash else 1
                    repeat_tracker[path_key] = (content_hash, count)
                    if count >= REPEAT_THRESHOLD:
                        stuck_repeating = True
                    mentioned_urls.update(extract_urls(fn_args["content"]))

                if fn_name == "run_command":
                    command = fn_args.get("command", "")
                    command_failed = result.get("status") != "ok" or result.get("exit_code") != 0
                    if command_failed:
                        failures = command_failure_counts.get(command, 0) + 1
                        command_failure_counts[command] = failures
                        if failures >= repeated_command_failure_threshold:
                            result["repeat_failure_feedback"] = (
                                f"This exact command has failed {failures} consecutive times in cwd "
                                f"{workdir.resolve()}. Do not run it again unchanged. Next action: "
                                "inspect the current directory with list_dir (and relevant files with read_file), "
                                "then adjust the command or the work."
                            )
                            # Hard stop, matching write_file's stuck_repeating: the model isn't
                            # adapting to the advisory above, so stop burning turn budget on it.
                            stuck_command = command
                    else:
                        command_failure_counts.pop(command, None)

                if fn_name == "web_fetch" and result.get("status") == "ok" and result.get("url"):
                    fetched_urls.add(result["url"].rstrip(_URL_TRAILING_PUNCT))

                print(f"{stderr_prefix}  -> {fn_name}({fn_args}) => {result}", file=sys.stderr)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result),
                })

            if finished:
                return True
            if stuck_repeating:
                crashed = (
                    f"wrote byte-identical content to '{path_key}' {REPEAT_THRESHOLD} times in a "
                    f"row despite failing feedback each time — model is not adapting, stopping "
                    f"early instead of burning the rest of the turn budget"
                )
                print(f"{stderr_prefix}[{phase} turn {phase_turn}] {crashed}", file=sys.stderr)
                return False
            if stuck_command:
                crashed = (
                    f"ran the exact same failing command {repeated_command_failure_threshold} times in a "
                    f"row despite failing feedback each time — model is not adapting, stopping "
                    f"early instead of burning the rest of the turn budget: {stuck_command}"
                )
                print(f"{stderr_prefix}[{phase} turn {phase_turn}] {crashed}", file=sys.stderr)
                return False
        return False

    def verify_completion(phase: str) -> tuple[bool, dict]:
        """Run the sole pass/fail authority and record a phase-labelled artifact."""
        print(f"{stderr_prefix}[verify {phase}] harness re-running: {verify_command}", file=sys.stderr)
        verify_result = execute_command(
            workdir, verify_command, shlex.split(verify_command)[:1], verify_timeout,
        )
        passed = verify_result.get("status") == "ok" and verify_result.get("exit_code") == 0
        record = {
            "phase": phase,
            "completion_kind": completion_kind,
            "passed": passed,
            "result": verify_result,
        }
        verification_results.append(record)
        log.append({
            "turn": "verify", "phase": phase, "kind": "verification",
            "response": {"content": None, "tool_calls": []},
            "verify_command": verify_command, "verify_result": verify_result,
        })
        print(
            f"{stderr_prefix}[verify {phase}] {'passed' if passed else 'FAILED'}",
            file=sys.stderr,
        )
        return passed, verify_result

    try:
        run_agent_phase(max_turns, "initial")

        if finished and verify_command:
            # The model's completion claim is never the last word — the harness re-runs
            # the real check itself, independent of anything the model reported mid-loop.
            passed, verify_result = verify_completion("initial")
            if not passed:
                initial_failure = {
                    "cause": "no_tool_prose" if completion_kind == "no_tool_prose" else "verification_failed",
                    "completion_kind": completion_kind,
                    "verification_result": verify_result,
                }
                finished = False
                if recovery_turns > 0:
                    messages.append({
                        "role": "user",
                        "content": (
                            "RECOVERY REQUIRED: the harness's mechanical verification after your previous "
                            "completion failed. Inspect the current files and use the available tools to edit "
                            "the workdir to correct the problem. Do not provide a prose patch or merely describe "
                            "a fix; make the edit with tools. Call finish only after making the correction.\n\n"
                            f"Verifier command: {verify_command}\n"
                            f"Verifier result: {json.dumps(verify_result, ensure_ascii=False)}"
                        ),
                    })
                    completion_kind = None
                    if run_agent_phase(recovery_turns, "recovery") and finished:
                        passed, recovery_verify_result = verify_completion("recovery")
                        if not passed:
                            finished = False
                            crashed = f"--verify-command failed after recovery: {recovery_verify_result}"
                    elif not crashed:
                        recovery_turn_limit_reached = True
                        crashed = (
                            f"recovery turn limit ({recovery_turns}) reached before a subsequent completion; "
                            "no final verification was run"
                        )
                else:
                    crashed = f"--verify-command failed: {verify_result}"

        if finished and "web_fetch" in allowed_tool_names:
            unverified = sorted(mentioned_urls - fetched_urls)
            if unverified:
                print(
                    f"{stderr_prefix}[web-verify] model's output mentions {len(unverified)} "
                    f"URL(s) never actually passed to web_fetch (claim without a real fetch): "
                    f"{unverified}",
                    file=sys.stderr,
                )
    except Exception as e:
        crashed = f"{type(e).__name__}: {e}"
        print(f"{stderr_prefix}CRASHED: {crashed}", file=sys.stderr)
    finally:
        write_logs()

    return {
        "finished": finished, "crashed": crashed, "finish_summary": finish_summary,
        "turns_used": total_model_turns,
        "unverified_urls": sorted(mentioned_urls - fetched_urls),
        "initial_failure": initial_failure,
        "verification_results": verification_results,
        "recovery_attempts": recovery_attempts,
        "recovery_turn_limit_reached": recovery_turn_limit_reached,
    }


def main():
    # Keep the original flag-only invocation intact. ``chat`` is an explicit
    # subcommand because no existing invocation accepted positional arguments.
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        chat_main(sys.argv[2:])
        return

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True, help="Sandbox root; all tool file paths are relative to this and cannot escape it")
    ap.add_argument("--system", default=None, help="Path to system prompt file (required unless --resolve-pending is given)")
    ap.add_argument("--task", default=None, help="Path to task/user prompt file (required unless --resolve-pending is given)")
    ap.add_argument("--resolve-pending", default=None, metavar="TOKEN", help="Skip the LLM loop entirely: look up <workdir>/pending-action.json left by a previous run's blocked destructive command, verify TOKEN matches and hasn't expired, then execute exactly that stored command (this one time bypassing the destructive-pattern check) and exit. Used to apply a human-approved action without re-invoking the model to decide again.")
    ap.add_argument("--max-turns", type=int, default=10)
    ap.add_argument("--model", default="qwen/qwen2.5-coder-14b")
    ap.add_argument("--base-url", default="http://localhost:1234/v1")
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--max-tokens", type=int, default=8000, help="Some models (e.g. reasoning models) burn thousands of tokens on reasoning_content before ever emitting a tool call — too low a cap causes a silent truncated stall")
    ap.add_argument("--reviewer-mode", action="store_true", help="Restrict tools to read_file/list_dir/write_verdict/finish — no write_file, so a reviewer can't mutate the file it's grading")
    ap.add_argument("--enable-run-command", action="store_true", help="Give the model a run_command tool to self-check its work (tests, render scripts). Not available in --reviewer-mode.")
    ap.add_argument("--enable-web-access", action="store_true", help="Dedicated restricted mode: write_file/read_file/list_dir/finish/web_search/web_fetch, no run_command. Requires TAVILY_API_KEY in the environment. Mutually exclusive with --reviewer-mode.")
    ap.add_argument("--allowed-commands", default="pytest,python3,node,npm,bd", help="Comma-separated list of binaries run_command is allowed to invoke")
    ap.add_argument("--command-timeout", type=int, default=60, help="Seconds before a run_command call is killed")
    ap.add_argument("--verify-command", default=None, help="Command the HARNESS (not the model) re-runs after a model completion. Nonzero exit overrides the model's claim — the model's own claim of success is never trusted alone.")
    ap.add_argument("--verify-timeout", type=int, default=120, help="Seconds before --verify-command is killed")
    ap.add_argument("--recovery-turns", type=int, default=0, help="Opt-in extra model turns after an initial harness-owned --verify-command failure. The verifier output is fed back to the model; a sample passes only if a later harness-owned verification exits 0. Default: 0 (disabled).")
    ap.add_argument("--bootstrap-workspace-context", action="store_true", help="Opt in to a bounded, sorted, metadata-only inventory of --workdir before the model's first action. Symlinks are excluded and paths are relative to --workdir.")
    ap.add_argument("--bootstrap-inventory-limit", type=int, default=DEFAULT_WORKSPACE_INVENTORY_LIMIT, help=f"Maximum filesystem entries inspected and shown by --bootstrap-workspace-context (default: {DEFAULT_WORKSPACE_INVENTORY_LIMIT}).")
    ap.add_argument("--bootstrap-include-verify-command", action="store_true", help="With --bootstrap-workspace-context, show the configured --verify-command as read-only harness configuration. Only use with a safe command that contains no secrets.")
    ap.add_argument("--repeated-command-failure-threshold", type=int, default=DEFAULT_REPEATED_COMMAND_FAILURE_THRESHOLD, help=f"After this many failures of the exact same run_command, add cwd and an inspect-directory hint to its feedback (default: {DEFAULT_REPEATED_COMMAND_FAILURE_THRESHOLD}).")
    ap.add_argument("--log-prefix", default=None, help="Basename for the log files (writes <prefix>.json/.md instead of harness-log.json/.md). Default: derived from --task's filename, so 'task.txt' -> 'harness-log.json' (unchanged) and 'task-b.txt' -> 'harness-log-b.json'. Set explicitly to avoid two runs in the same --workdir silently overwriting each other's log.")
    ap.add_argument("--samples", type=int, default=1, help="Run this many independent attempts instead of one. Each attempt gets its own isolated sub-workdir (<workdir>/samples/sample-N/) so write_file calls can't collide between attempts. Requires --verify-command to be useful — that's what separates passing from failing samples; a <log-basename>-samples.json summary is written listing every attempt's outcome. Exit code 0 if at least one sample passed. IMPORTANT: --verify-command runs with cwd=<that sample's subdir>, not --workdir — give any checker SCRIPT as an absolute path (the file it checks, e.g. output.html, stays relative so it resolves inside the sample).")
    args = ap.parse_args()

    if args.recovery_turns < 0:
        ap.error("--recovery-turns must be zero or greater")
    if args.bootstrap_inventory_limit < 1:
        ap.error("--bootstrap-inventory-limit must be at least 1")
    if args.repeated_command_failure_threshold < 1:
        ap.error("--repeated-command-failure-threshold must be at least 1")
    if args.bootstrap_include_verify_command and not args.bootstrap_workspace_context:
        ap.error("--bootstrap-include-verify-command requires --bootstrap-workspace-context")
    if args.bootstrap_include_verify_command and not args.verify_command:
        ap.error("--bootstrap-include-verify-command requires --verify-command")

    allowed_bins = [b.strip() for b in args.allowed_commands.split(",") if b.strip()]

    if args.resolve_pending:
        top_workdir = Path(args.workdir)
        top_workdir.mkdir(parents=True, exist_ok=True)
        result = resolve_pending_action(top_workdir, args.resolve_pending, allowed_bins, args.command_timeout)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("status") == "ok" and result.get("exit_code") == 0 else 1)

    if not args.system or not args.task:
        ap.error("--system and --task are required unless --resolve-pending is given")

    if args.enable_web_access and args.reviewer_mode:
        ap.error("--enable-web-access and --reviewer-mode are mutually exclusive")

    if args.enable_web_access:
        check_tavily_key_or_die()
    if args.enable_web_access:
        tools = list(WEB_TOOLS)
    else:
        tools = list(REVIEWER_TOOLS if args.reviewer_mode else TOOLS)
        if args.enable_run_command and not args.reviewer_mode:
            tools.append(make_run_command_tool(allowed_bins))
    allowed_tool_names = {t["function"]["name"] for t in tools}

    top_workdir = Path(args.workdir)
    top_workdir.mkdir(parents=True, exist_ok=True)
    system_prompt = Path(args.system).read_text(encoding="utf-8")
    task_prompt = Path(args.task).read_text(encoding="utf-8")
    log_basename = derive_log_basename(args.task, args.log_prefix)

    common = dict(
        system_prompt=system_prompt, task_prompt=task_prompt, tools=tools,
        allowed_tool_names=allowed_tool_names, allowed_bins=allowed_bins,
        model=args.model, base_url=args.base_url, temperature=args.temperature,
        max_tokens=args.max_tokens, max_turns=args.max_turns,
        command_timeout=args.command_timeout, verify_command=args.verify_command,
        verify_timeout=args.verify_timeout, log_basename=log_basename,
        recovery_turns=args.recovery_turns,
        workspace_bootstrap=args.bootstrap_workspace_context,
        workspace_inventory_limit=args.bootstrap_inventory_limit,
        bootstrap_include_verify_command=args.bootstrap_include_verify_command,
        repeated_command_failure_threshold=args.repeated_command_failure_threshold,
    )

    if args.samples <= 1:
        result = run_once(workdir=top_workdir, **common)
        pending_path = top_workdir / PENDING_ACTION_FILENAME
        if pending_path.is_file():
            pending = json.loads(pending_path.read_text(encoding="utf-8"))
            print(
                f"PENDING CONFIRMATION: {pending['command']!r} (token {pending['token']}) -- "
                f"{pending['reason']}",
                file=sys.stderr,
            )
            sys.exit(4)
        if result["crashed"]:
            sys.exit(2)
        if not result["finished"]:
            print(f"FAILED: exhausted {args.max_turns} turns without finish()", file=sys.stderr)
            sys.exit(1)
        print(f"DONE: {result['finish_summary']}", file=sys.stderr)
        return

    if not args.verify_command:
        print(
            "WARNING: --samples > 1 with no --verify-command — every sample that merely calls "
            "finish() counts as 'passed' with nothing independently checking its work. "
            "Add --verify-command to actually filter samples.",
            file=sys.stderr,
        )

    results = []
    for i in range(1, args.samples + 1):
        sample_dir = top_workdir / "samples" / f"sample-{i}"
        sample_dir.mkdir(parents=True, exist_ok=True)
        r = run_once(workdir=sample_dir, stderr_prefix=f"[sample {i}/{args.samples}] ", **common)
        r["sample"] = i
        r["workdir"] = str(sample_dir.relative_to(top_workdir))
        r["passed"] = bool(r["finished"] and not r["crashed"])
        results.append(r)
        print(f"[sample {i}/{args.samples}] {'PASS' if r['passed'] else 'FAIL'}", file=sys.stderr)

    passed = [r for r in results if r["passed"]]
    summary_path = top_workdir / f"{log_basename}-samples.json"
    summary_path.write_text(json.dumps({"samples": results, "passed_count": len(passed), "total": len(results)}, indent=2), encoding="utf-8")
    print(f"\n{len(passed)}/{len(results)} samples passed. Summary: {summary_path}", file=sys.stderr)
    for r in results:
        print(f"  sample-{r['sample']}: {'PASS' if r['passed'] else 'FAIL'} ({r['workdir']})" + (f" — {r['crashed']}" if r['crashed'] else ""), file=sys.stderr)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
