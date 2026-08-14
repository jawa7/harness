# harness: unstuck-behavior, context, and memory design spec

Destination artifact of the [harness evolution](.scratch/harness-evolution/map.md)
Wayfinder map. This is a **plan, not a build** — it names what to build, in
what order, and why, so a follow-on execution effort can implement directly
from it without re-deriving the reasoning. No code changes were made as
part of writing this document.

## Why this exists

`harness` (`qwen_agent.py`) currently passes ~73% of isolated, single-file
eval cases but **0%** of repo-context, multi-file cases
(`evals/python-coder`, comparing `loc-25-clean-20260813T162231` and
`loc-25-recovery2-20260813T164655`). Root-causing that gap — and the
research it prompted — is what this spec is built from. See
`.scratch/harness-evolution/map.md` for the full decision trail; this
document is the synthesis, not a replacement for it.

## Prerequisite: three bugs, tracked separately, not part of this spec

These are already root-caused with a clear fix shape and don't need further
design — they're a separate, immediate bugfix task (decided outside this
map), listed here only because everything below assumes they're fixed first:

1. **Prose-only fake edits**: the model describes an edit in prose without
   ever calling `write_file`.
2. **Identical-retry loop**: the model repeats a verbatim-identical
   `run_command`/`write_file` call across turns.
3. **`summarize.py`'s `classify_failure` mislabels an outright API crash**
   (e.g. `HTTPError: HTTP Error 400` before turn 1) as `prose-only`, because
   it only checks for the absence of tool calls — masking real request
   failures under a model-behavior category.

## Roadmap

### Tier 0 — Configuration correctness (do first, cheap, unblocks accurate measurement)

- **Explicitly set the LM Studio context window to 32,768 tokens** for both
  `qwen2.5-coder-7b-instruct-mlx` and `-3b-instruct-mlx`; do not rely on
  auto-detection. LM Studio is documented to silently fall back to as
  little as 2,048 tokens for MLX-converted models it can't read metadata
  for — a plausible root cause of the `loc-25-3b` run's 22/24 `HTTP 400`
  failures. ([context-length-policy](.scratch/harness-evolution/research/03-context-length-policy.md), B4/A4)
- **Add `try/except` around `call_model()`'s `urlopen()` call**
  (`qwen_agent.py:826-842`). A context-length-exceeded 400 currently crashes
  the run as an unhandled `HTTPError` instead of becoming a diagnosable,
  loggable outcome. This alone turns today's ambiguous "prose-only vs.
  crashed" collection failures into a distinguishable, countable category —
  and is a prerequisite for bug #3's fix above to have anything meaningful
  to classify.
- 32K is the target, not a placeholder: it's Qwen2.5-Coder-7B/3B's native
  trained maximum, and mlx-lm doesn't support the YaRN scaling needed to go
  higher on this stack. It should not differ by case kind or model size —
  real transcripts already reach 5,000-11,000 tokens by turn 16 and were
  still climbing, so 32K buys real headroom rather than being immediately
  re-exhausted.

### Tier 1 — Unstuck-behavior core (the direct fix for the 0% repo-context rate)

From [unstuck-behavior-decision](.scratch/harness-evolution/issues/04-unstuck-behavior-decision.md),
in build order:

1. **Widen and wire in tool-call synthesis fallback.**
   `normalize_content_tool_calls()` already exists in `qwen_agent.py` but is
   only wired into chat mode (`run_chat_turn`), not the eval-harness loop
   (`run_once`) that has the bug — and it's too narrow (strict JSON-array
   dialect only) to catch the actual observed failure shape (prose +
   markdown code fence). Broaden it to an Albatross-style detector and wire
   it into `run_once`'s no-tool-calls branch, before falling through to
   `completion_kind = "no_tool_prose"`. This is the direct, structural fix
   for bug #1 above, not just a mitigation.
2. **Give `run_command` the hard-stop `write_file` already has.**
   `stuck_repeating` already hard-stops the phase after 3 byte-identical
   `write_file` calls. `run_command` only gets a soft, ignorable
   `repeat_failure_feedback` advisory. Add the matching hard stop after
   `repeated_command_failure_threshold` consecutive identical-command
   failures. This is the direct fix for bug #2.
3. **One unified plan/tried-state tool.** A lightweight `update_plan`-style
   tool call the model uses to declare/update subtasks and record failed
   attempts, rendered into every subsequent turn's context. Serves two
   purposes as one mechanism: gives the model's own reasoning somewhere to
   put a task decomposition, and gives it a compact "what I already tried
   and why it failed" memory — replacing the current situation where that
   information only exists implicitly in a growing raw transcript. Not RAG,
   not a second model.

### Tier 2 — Context management (supports Tier 1's larger transcripts)

From [context-length-policy](.scratch/harness-evolution/research/03-context-length-policy.md):

- Make `trim_chat_messages()` token-aware (in addition to its current
  message-count window) — a single large `read_file` result can already
  consume a meaningful fraction of budget, and the 20-message window
  provides no protection against that.
- Re-check `--max-turns` now that the two hard-stops above exist:
  repo-context transcripts hadn't plateaued by turn 16 in measured runs, so
  part of the 0% repo-context rate may simply be "ran out of turns" —
  something no retry logic fixes if the ceiling itself is the constraint.

### Tier 3 — Same-repo persistent memory (real, non-eval usage)

From [persistent-memory-decision](.scratch/harness-evolution/issues/05-persistent-memory-decision.md):

- **Handoff artifact.** On top of the already-existing
  `save_chat_session`/`load_chat_session`, add a compact structured note
  (done / in-progress / key decisions / next steps / key files) written at
  session end, read back in at the next session's start on the same repo —
  Albatross `continue.md` / LocalHarness capped-summary shape. Small, no new
  infrastructure.
- **Repo-map / file-tree navigation** for repos too large to fit in 32K —
  Aider- and moatless-tools-style static/iterative navigation, no
  embeddings. First-line mechanism for the "big repo" problem.
- Cross-eval-sample and cross-task memory remain explicitly **out of
  scope** — sample isolation is what makes `evals/python-coder`'s
  N-samples-per-case statistics comparable; do not build shared memory
  across samples.

### Tier 4 — Deferred, gated, or optional (do not build without a trigger)

- **Pluggable on-demand second-opinion model** (04, mechanism 4). Local
  gemma e2b/e4b (reusing the `gemma_review.py` precedent of a second model
  at the same LM Studio endpoint) and cloud gpt-oss-20b via OpenRouter's
  free tier, as interchangeable backends. Scoped narrowly: an escalation
  path for novel, context-specific dead-ends a fixed rule can't describe
  (e.g. syntactically-correct code that misreads the task's actual
  requirement) — not a first responder, since Tier 1's deterministic
  mechanisms handle the *known* failure shapes more cheaply and reliably.
  Build only after Tier 1-3 ship and a residual gap is actually measured.
  The OpenRouter backend adds a first-of-its-kind reasoning network
  dependency and needs `Authorization` header support `call_model()`
  doesn't have today; decide before building whether automated eval runs
  are ever allowed to hit it unattended (free-tier rate-limit risk) or it
  stays opt-in/manual-only.
- **Self-authored ephemeral sub-harness.** Resolved to "no new capability
  needed" — `run_command`+`write_file` already permit scratch scripts
  within the existing `--allowed-commands` whitelist. If ever prioritized,
  it's a small system-prompt permission/encouragement note plus a
  pre-`finish` cleanup convention, not a new mechanism.
- **Full embeddings-based RAG.** Documented upgrade path for Tier 3's
  repo-map mechanism, not built now. Gate it behind a concrete, measurable
  trigger (e.g. a real repo whose repo-map itself doesn't fit in a
  reasonable context budget) — no current harness usage has hit that
  ceiling, and none of the 6 harnesses surveyed for this decision use
  embeddings/vector-store RAG for this problem class.

## What this spec deliberately does not cover

- Implementation details (exact tool schemas, exact prompt text, exact code
  diffs) — that's the follow-on build effort's job, informed by this
  document.
- The three-bug fix task's own design — already root-caused, tracked
  separately, assumed done before Tier 1 starts.
- A committed trigger threshold for the Tier 4 RAG upgrade, or a firm
  decision on whether eval runs may call the OpenRouter backend
  unattended — both flagged as open in
  [map.md's "Not yet specified"](.scratch/harness-evolution/map.md), to be
  pinned down by the build effort or a future map.
