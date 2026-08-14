# Unstuck-behavior decision

Type: grilling
Status: resolved
Blocked by: 01, 02

## Question

Given findings from [[01-video-best-practices]] and
[[02-github-harness-survey]], and given that the already-diagnosed
prose-only-edit and identical-retry-loop bugs are being fixed separately
(see map Notes) — what concrete mechanism(s), if any, should `harness` adopt
to behave less "stuck" on tasks it can't immediately solve? Candidates from
the original brain dump, treated as competing/combinable options rather than
four independent commitments:

- Self-decomposition: model breaks a stuck task into smaller subtasks itself.
- Internet access: model can fetch/read documentation mid-task when stuck.
- Steering pairing: a smaller model (gemma e2b/e4b) watches the coder model
  and nudges it toward tool calls / internet use when it's not adapting.
- Self-authored ephemeral sub-harness: the coder model (or the gemma
  steerer, if the coder can't reason about this itself) decides what
  tooling/scaffolding it needs for the specific task, builds a throwaway
  harness for it, uses it, then deletes it.

Resolve to a specific, justified choice (adopt one, combine several, or
defer/reject with reasons) — not a menu.

## Answer

Adopt all four, each scoped concretely against the actual `qwen_agent.py`
code (not the abstract candidate list):

**1. Widen the tool-call synthesis fallback and wire it into `run_once`.**
`normalize_content_tool_calls()` already exists (line 1039) but is only
wired into `run_chat_turn` (chat mode), not `run_once` (the eval-harness
loop that actually has the bug). It's also too narrow — it only catches a
strict JSON-array dialect, not the prose-with-markdown-code-fence shape
actually observed in the diagnosed bug (`"### Modified test_cli.py:\n\`\`\`python\n..."`).
Build a broader Albatross-style detector (does the content contain something
shaped like "a file path + a code block that's clearly meant to replace that
file's content"?) and call it from `run_once`'s no-tool-calls branch before
falling through to `completion_kind = "no_tool_prose"`.

**2. Give `run_command` the same hard-stop `write_file` already has.**
`stuck_repeating` already hard-stops the phase after 3 byte-identical
`write_file` calls to the same path (`REPEAT_THRESHOLD`). `run_command` has
no equivalent — `command_failure_counts` only ever appends a soft
`repeat_failure_feedback` advisory string to the tool result, which the
model can (and did, in the diagnosed bug) ignore indefinitely up to
`--max-turns`. Add a matching hard stop: after
`repeated_command_failure_threshold` consecutive identical-command failures,
end the phase the same way `stuck_repeating` does, instead of only nagging.

**3. One unified plan/tried-state, not two separate mechanisms.**
The "what's been tried" note (OpenHands condenser, Albatross `continue.md`)
and self-decomposition (model reasons about the task and breaks it into
subtasks for itself, harness just gives it somewhere to put that) turn out
to be the same underlying need: a structured, harness-maintained state
object (goal / subtask list with status / what's been tried and why it
failed) that's re-injected into context every turn, instead of expecting the
model to re-derive its plan and its memory of failed attempts implicitly
from a growing raw transcript. Concretely: a lightweight `update_plan`-style
tool the model calls to declare/update subtasks and record a failed attempt,
whose current state gets rendered into every subsequent turn's context —
not a second model, not RAG, just structured state.

**4. Pluggable second-opinion model — on-demand, not first-line.**
Scoped narrowly per this session's discussion: not a first responder for
every stuck moment (mechanisms 1-3 handle the *known* failure shapes
deterministically and more cheaply/reliably than a second small model
could), but an escalation path for *novel, context-specific* dead-ends a
fixed rule can't describe — e.g. the model writes syntactically correct code
that satisfies the verifier's letter but misreads the task's actual
requirement. Invoked only when mechanisms 1-3 don't resolve a stuck state
within budget.

Must support two interchangeable backends, both scoped by this session:
- **Local**: gemma e2b/e4b via the same LM Studio OpenAI-compatible endpoint
  `harness` already talks to. There's already a working precedent for this
  exact pattern in this repo — `gemma_review.py` calls a second local model
  (`google/gemma-4-e2b`) at the same `base_url` shape, just a different
  `model` string. `call_model()` already takes `base_url`/`model` as
  parameters, so pointing it at a second loaded model is close to free.
- **Cloud**: gpt-oss-20b via OpenRouter (free tier as of this session).
  Genuinely new surface: OpenRouter needs an `Authorization: Bearer <key>`
  header, and `call_model()` currently sends no auth headers at all (checked
  directly — line 826-842). This is also the harness's first *reasoning*
  network dependency (existing `WEB_TOOLS`/`web_fetch` only let the primary
  model fetch documentation pages, not call a second reasoning model over
  the network) — worth flagging explicitly in the spec as a scope
  boundary, and worth deciding whether eval runs ever hit it automatically
  (free-tier rate limits could break an unattended eval batch) or whether
  it's opt-in/manual-only for now.

Self-authored ephemeral sub-harness keeps its own line item, but the
analysis stands: `run_command`+`write_file` already let the model write and
run scratch scripts within the `--allowed-commands` whitelist today — no new
tool/capability is needed. What's actually missing is (a) explicit
system-prompt permission/encouragement to do this when stuck, and (b) a
cleanup convention (delete scratch files before `finish` so they don't
pollute `--verify-command`'s view of the workdir).
