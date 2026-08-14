# GitHub/web survey: harnesses for small local coding models

Scope per the ticket: projects with a specific angle on getting *7B-class-and-below,
locally-served* models (MLX/llama.cpp/Ollama/vLLM) to reliably complete multi-file,
repo-context coding tasks — not generic cloud-agent frameworks, unless they have a
concrete local/small-model angle worth stealing from. `harness` itself is
`qwen_agent.py`: a single-model tool-calling loop (`list_dir`/`read_file`/`write_file`/
`run_command`), a post-`finish` recovery-turns repair budget, and a
propose/confirm/execute gate for destructive commands. The known gap this survey feeds:
the model fake-edits in prose without calling `write_file`, or loops on an identical
command/edit — both happen *before* `finish`, so today's recovery mechanism can't reach
them (that specific bug is being fixed separately; this ticket is about complementary
mechanisms).

Note on method: several results returned by general web search for "local model
harness" are very new, small repos (created 2026, low star counts) that read as
marketing/SEO-tool-directory content. I verified each one actually exists via the
GitHub API before including it (`gh api repos/<owner>/<repo>`), and fetched real READMEs
rather than trusting search-engine summaries. Two such repos (Albatross, LocalHarness)
turned out to have genuinely relevant, specific content and are included below with that
caveat flagged. A third ("LocalAgent" / PrAsAnNaRePo/LocalAgent, 22 stars, last pushed
Dec 2023) was excluded as abandoned and too thin to be useful.

---

## 1. SWE-agent — Princeton (agent-computer interface)

- **URL**: https://github.com/SWE-agent/SWE-agent · paper: https://arxiv.org/abs/2405.15793
- **Scale/activity**: ~20,000 stars, pushed 2026-08-10 (actively maintained).
- **Problem/architecture**: Not local-model-specific by default, but its central thesis
  is directly on-topic: model-agnostic "agent-computer interfaces" (ACI) — a small,
  constrained action space for viewing/searching/editing files, instead of a raw shell —
  because a bounded action space with built-in guardrails measurably improves success
  rate for *any* model, and the effect is largest for weaker models. Quote (paper): the
  ACI gives "a small set of simple actions for viewing, searching through and editing
  files, with guardrails to prevent common mistakes and specific, concise feedback about
  a command's effects at every turn."
- **Stuck handling**: No retry/escalation layer per se — the mechanism *is* the
  interface. Every action returns concise, structured feedback about what happened,
  which is the design lever they use to keep weaker models from drifting: less freedom
  to emit an ambiguous/malformed action in the first place, and immediate, legible
  feedback when one is attempted.
- **Context/memory**: Flat context, localization/editing/testing are separate concerns
  built into the interface, not into an external memory system.
- **Assessment**: **Borrow the interface-design idea specifically, not the codebase.**
  `harness`'s current tool set (`list_dir`/`read_file`/`write_file`/`run_command`) is
  already conceptually close to an ACI; the actionable takeaway is SWE-agent's insistence
  on *forced, specific per-turn feedback* on tool outcomes — worth auditing whether
  `harness`'s current tool results are as concrete/actionable as SWE-agent's, since a
  vague "write succeeded" gives a small model less signal to avoid repeating a broken
  edit than "wrote 42 lines; previous write to this file 30s ago was identical."
  Source: https://arxiv.org/abs/2405.15793

## 2. Aider — architect/editor model split

- **URL**: https://github.com/Aider-AI/aider
- **Scale/activity**: ~48,200 stars, pushed 2026-05-22 (actively maintained).
- **Problem/architecture**: General AI pair-programmer; supports any OpenAI-compatible
  endpoint including local Ollama models. The specific angle relevant here is
  **"architect mode"**: the primary (often stronger/cloud) model proposes *what* to
  change in prose, and a second, separate **editor model** — which can be a smaller,
  cheaper, locally-run model — is given only that proposal and asked to turn it into
  the actual file edits in a fixed diff format. Source:
  https://aider.chat/docs/usage/modes.html
- **Stuck handling**: Splits "decide what to do" from "mechanically apply it" across two
  model calls, on the theory (documented in Aider's own docs) that frontier/reasoning
  models "reason brilliantly but sometimes mangle structured diff output," while a
  smaller, more literal model is "precise about diffs but weaker at planning." This is
  a structural fix for exactly the harness's "model describes an edit in prose instead
  of emitting a tool call" failure mode — if editing is the *only* thing the second
  model is asked to do, from an unambiguous spec, there's much less room for it to drift
  into prose-only description.
- **Caveat found in Aider's own issue tracker**: the editor model only receives the
  architect's last message, not the full conversation, so context can be lost between
  the two calls (https://github.com/Aider-AI/aider/issues/3287); documented editing
  failures still happen with weak/quantized local editor models
  (https://aider.chat/docs/troubleshooting/edit-errors.html: "quantized local models are
  more likely to have editing problems because they are not capable enough to follow
  aider's system prompts").
- **Context/memory**: Aider's `repo map` (a static, token-budgeted summary of repo
  structure/symbols) is its context mechanism for multi-file awareness — closer to
  `harness`'s existing `--bootstrap-workspace-context` metadata-only inventory than to
  RAG. No long-term persistent memory across sessions.
- **Assessment**: **Borrow the architect/editor pattern's core idea**, adapted: rather
  than two separate models, this maps onto `harness`'s existing recovery-turns concept —
  if the failure is specifically "wrote prose instead of a tool call," a variant is a
  second, narrowly-scoped call ("here is your own prose description of an edit; turn it
  into exactly one `write_file` call, nothing else") rather than a full-context repeat.
  Cheaper and more targeted than swapping models. Sources:
  https://aider.chat/docs/usage/modes.html,
  https://github.com/Aider-AI/aider/issues/3287,
  https://aider.chat/docs/troubleshooting/edit-errors.html

## 3. OpenHands (formerly OpenDevin)

- **URL**: https://github.com/OpenHands/OpenHands
- **Scale/activity**: ~84,000 stars, pushed 2026-08-14 (very actively maintained).
- **Problem/architecture**: General autonomous SWE agent platform; models the agent as
  "a pure function from event history to next event, run in a loop" — every action and
  observation is an immutable, replayable, typed event. Explicitly documents small/local
  model support via Ollama/vLLM, with specific model recommendations (`qwen2.5-coder:3b`,
  quantized `llama3.1:8b-instruct-q4_K_M`) for constrained hardware. Source:
  https://docs.openhands.dev/openhands/usage/llms/llms
- **Stuck handling**: No explicit small-model-specific unstuck logic found beyond
  general error/observation handling in the event loop; the event-sourced design does
  make replay/audit/fork of a stuck run possible, which is more of a debugging aid than
  an autonomous recovery.
- **Context/memory — the actually relevant part**: OpenHands' **context condenser** is
  the most transferable idea here. When the visible event count crosses a threshold, an
  LLM call summarizes everything except the first few events (system prompt/task) and
  the last few (recent work) into a compact summary that explicitly preserves "the
  user's goals, the progress the agent has made, and what still has to be done" plus,
  for SWE tasks specifically, "critical files and failing tests." Three trigger modes:
  proactive (threshold-based), per-turn, and reactive (only on a context-overflow
  error). Sources: https://docs.openhands.dev/sdk/guides/context-condenser,
  https://www.openhands.dev/blog/openhands-context-condensensation-for-more-efficient-ai-agents
- **Assessment**: **Borrow the condenser's summary shape**, not the mechanism. The
  three-part frame (goal / progress-so-far / remaining-work, plus explicitly-preserved
  file list) is a good structured target for a lightweight persistent-memory or
  mid-run-recovery note in `harness`, cheaper than full RAG and directly aimed at
  "what has this run already tried" — which is exactly the blind spot in the loop-repeat
  bug (the model currently has no compact record telling it "you already wrote this
  exact content and it didn't fix anything").

## 4. moatless-tools / moatless-tree-search — SWE-bench context-management research

- **URL**: https://github.com/aorwall/moatless-tools · https://github.com/aorwall/moatless-tree-search · paper (SWE-Search): https://arxiv.org/abs/2410.20285
- **Scale/activity**: moatless-tools 641 stars, last pushed 2025-09-01; moatless-tree-search
  142 stars, last pushed 2025-06-06. Both **stalled, not actively developed** (11-14
  months with no push as of this survey) — research-grade code, not a maintained product.
- **Problem/architecture**: Built specifically around the observation that most
  SWE-bench repos contain far more context than any model's window, including small
  models. Instead of embeddings/RAG, it uses **iterative expansion from the file tree**
  plus explicit tools for browsing files — a structured-navigation alternative to vector
  search. moatless-tree-search adds Monte Carlo Tree Search on top: each state (partial
  solution) branches into a capped number of expansions (max 3), evaluated by a value
  function, with total search iterations capped (100).
- **Stuck handling**: The tree-search variant *is* the stuck-handling mechanism —
  instead of a single linear trajectory that can loop, it explores multiple candidate
  next-actions per state and scores them, backtracking away from unproductive branches.
  Measured gains of +17% to +27% over a single-trajectory baseline across several
  models including Qwen2.5-72B (larger than harness's target class, but the mechanism
  is model-size-agnostic).
- **Context/memory**: File-tree navigation instead of RAG — deliberately no vector DB.
- **Assessment**: **Skip adopting the code (stale, and MCTS is heavyweight — multiple
  full trajectories per task, expensive for a 7B model where the whole point is fast
  local iteration), but the underlying idea is worth flagging for
  [[05-persistent-memory-decision]]**: "iterative file-tree navigation instead of
  vector search" is a much cheaper context mechanism than RAG and may fit a small local
  model's needs better than embeddings would. The tree-search/backtracking idea is
  worth a lighter-weight nod for [[04-unstuck-behavior-decision]] (e.g., "if the same
  action was already tried and failed, don't just retry it — the harness already has a
  primitive version of this via `--repeated-command-failure-threshold`).
  Sources: https://arxiv.org/abs/2410.20285, https://github.com/aorwall/moatless-tools,
  https://github.com/aorwall/moatless-tree-search

## 5. Albatross — terminal-first, explicitly local-model-first coding harness

- **URL**: https://github.com/morganlinton/Albatross
- **Scale/activity**: 219 stars, 23 forks, pushed 2026-08-03, created 2026-04-25 — small
  but real and active (verified via `gh api`; not a fork, has issues/PRs/MIT license).
  Rust, "harness"/"mlx"/"llama-cpp"/"ollama" among its own GitHub topics.
- **Problem/architecture**: Terminal harness explicitly designed to run identically
  against cloud models or fully local ones (Ollama/MLX/llama.cpp), with per-hardware
  model recommendations (`/doctor recommend`) and llama.cpp-specific warmup (sends a
  1-token request with the full system prompt + tools at startup "so llama.cpp-derived
  engines have a hot prompt-eval cache").
- **Stuck handling — most directly relevant finding of this survey**: Albatross
  explicitly handles the exact failure class `harness` is trying to fix. Quote from its
  README: **"When models emit tool calls as text JSON, the system detects and
  synthesizes a real tool call."** This is a template-matching fallback layer that
  parses a model's prose/JSON-in-content output for something shaped like a tool call
  and converts it into a real one, rather than treating it as a failed turn. It also has
  a `/doctor --deep` probe that specifically tests whether a given local model/backend
  combination emits "streaming, usage chunks, native tool calls, [or] inline JSON
  fallback" — i.e., it treats "does this model actually call tools correctly" as a
  measurable, per-model/per-backend property to check up front, not just something to
  react to at runtime. Separately, `/iterate` runs a generate→evaluate→improve loop with
  a critic model scoring against a rubric, and `/route select`/`/plan route` can escalate
  from a small/cheap model to a larger one when needed.
- **Context/memory**: `/index` builds a "safe local repo map" — metadata only
  (paths/language/symbols/headings/capped keywords), never file bodies, similar in
  spirit to `harness`'s `--bootstrap-workspace-context`. `/reset` writes a structured
  handoff artifact (`.albatross/continue.md`: done / in-progress / key decisions / next
  steps / key files) before starting a fresh context window, and a compaction mode
  keeps "a clean context window carrying just what's needed to continue" rather than
  summarizing in place.
- **Assessment**: **Directly relevant, small-scale but worth borrowing two specific
  practices from, not adopting wholesale** (it's a full Rust harness with far more
  surface area than `harness` needs, and 219 stars / 4 months old means it hasn't been
  battle-tested at scale): (1) the text-JSON-as-tool-call synthesis fallback maps almost
  exactly onto the harness's known "model fake-edits in prose without a tool call" bug,
  and (2) the `continue.md`-style structured handoff note is a light, concrete shape for
  persistent memory. Source: https://github.com/morganlinton/Albatross (README fetched
  directly from repo, verified real via `gh api repos/morganlinton/Albatross`).

## 6. LocalHarness — YAML agent-definition harness for local OpenAI-compatible endpoints

- **URL**: https://github.com/ahwurm/localharness
- **Scale/activity**: 18 stars, 1 fork, pushed 2026-08-14 (today, actively developed),
  created 2026-05-24. Python, MIT. Small and early — flagging the low star count
  honestly; included because its actual content is unusually specific to this survey's
  question, verified via `gh api repos/ahwurm/localharness`.
- **Problem/architecture**: Positions itself as "the agent layer on top of your
  inference engine: vLLM, Ollama, LM Studio, or llama.cpp" — bridging the gap between
  frontier cloud agents (rate-limited, unsuitable for background/recurring local work)
  and raw local model serving (no tools/memory/permissions layer). Agents defined in
  YAML with deny-first permission inheritance.
- **Stuck handling**: The standout idea: a **"write gate"** that scores every tool call
  outcome against that specific tool's own history using **per-tool statistical priors
  computed in pure SQL — zero additional model calls**. A normally-reliable tool that
  starts erroring becomes a flagged/captured signal; expected/routine failures get
  deprioritized below the prompt visibility line instead of cluttering context, while
  unexpected failures get surfaced for consolidation into memory. This is a cheap,
  deterministic (non-LLM) unstuck-adjacent signal rather than a retry-loop or
  escalation mechanism per se.
- **Context/memory**: Multi-layered but SQLite-only by design — explicitly "no vector
  DB, no second resident model." A gist/schema hierarchy routes search to the right
  leaf records. Session continuity: each run closes with one auto-generated topical
  summary line; the next session's prompt opens with a hard-capped (8-line), newest-
  first recent-timeline of those summaries. Also does lossless long-document reading
  (never truncates, every number traces to source) via chunked processing rather than
  truncation-with-loss.
- **Assessment**: **Borrow the write-gate idea specifically** — it's cheap (SQL only,
  no extra model calls) and directly usable as an unstuck signal: if `harness` tracked
  simple per-tool/per-args success/failure history, it could pre-empt the identical-
  retry-loop bug by refusing (or flagging) a `run_command`/`write_file` call that
  matches a recently-failed one, which is strictly cheaper than the existing
  `--repeated-command-failure-threshold` counter and could subsume it. The 8-line
  capped session-summary pattern is also a good minimal-viable shape for persistent
  memory if `harness` ever runs multi-session work on the same repo. Given its youth
  (3 months old, 18 stars), don't adopt the codebase — borrow the two ideas. Source:
  https://github.com/ahwurm/localharness (README fetched directly, verified via
  `gh api repos/ahwurm/localharness`).

## 7. Real-world evidence: documented small-local-model tool-calling failure modes (Cline/Roo-Code issue trackers)

Not a harness to adopt, but load-bearing evidence for *why* this class of bug is common
and well-understood elsewhere, which validates that `harness`'s known bug isn't an
outlier:

- **Cline #10843**: Qwen2.5-Coder via a local endpoint correctly emits tool calls as
  JSON, but Cline's parser is hard-coded to expect Anthropic-style XML tags, so it never
  recognizes them and loops. https://github.com/cline/cline/issues/10843
- **Cline #11263**: "Infinite JSON tool loop" specifically on Ollama with tool-capable
  models — Ollama forces JSON tool-call output but the client-side stream parser doesn't
  handle it, causing an infinite loop rather than a clean failure.
  https://github.com/cline/cline/issues/11263
- **Roo-Code #8140 / related reporting**: Same root cause pattern — Ollama returns a
  native JSON tool-call object, Roo Code's stream receiver expects XML-tagged tool calls
  inline in text, mismatch throws `MODEL_NO_TOOLS_USED` and loops indefinitely; fix
  proposed is to check the model's actual declared tool-calling capability before
  choosing which output format to expect. https://github.com/RooCodeInc/Roo-Code/issues/8140
- **Context-window truncation as a second, distinct cause of apparent "loops"**: Ollama
  silently truncates/discards context past a small default window unless `num_ctx` is
  raised via a Modelfile; multiple independent sources report this as "the single most
  impactful reliability fix" for tool-calling agents on Ollama, because the *symptom*
  (agent seems to forget what it already tried, repeats itself) looks identical to a
  genuine reasoning loop but has a completely different, purely-configuration fix.
  https://aider.chat/docs/troubleshooting/edit-errors.html (context window heading),
  general pattern corroborated across https://localaimaster.com/blog/build-local-ai-agent
- **Assessment**: Not adoptable code, but a concrete checklist item: before attributing
  a repeat-loop to "the model got stuck," rule out format-mismatch (JSON vs XML
  expectations) and context-window truncation as root causes — both produce identical
  surface symptoms to genuine reasoning failure but need entirely different fixes.

## 8. Supporting context (not a harness): NVIDIA, "Small Language Models are the Future of Agentic AI"

- **URL**: https://arxiv.org/abs/2506.02153 (also https://research.nvidia.com/labs/lpr/slm-agents/)
- Position paper (Belcak et al., June 2025), not code, but relevant as grounding: argues
  models under ~10B parameters are "sufficiently powerful, inherently more suitable, and
  necessarily more economical" for most agentic sub-tasks, specifically citing that SLMs
  are "fast, low cost, and very reliable about output format" for narrow, well-specified
  steps — an argument for *keeping tasks narrow and well-specified* rather than asking a
  7B model to do open-ended multi-file reasoning in one shot, which is consistent with
  what actually seems to break in `harness`'s repo-context cases.
- **Assessment**: No code to adopt; cite as justification for narrowing/decomposing
  repo-context tasks into smaller, more mechanically-checkable steps (aligns with this
  repo's own `README.md` finding that a "locked-skeleton" brief with almost nothing left
  to decide costs far fewer regeneration rounds than an "open-skeleton" one).

---

## Synthesis: most promising practices for `harness`

1. **Tool-call synthesis fallback for prose/JSON-shaped edits** (source: Albatross's
   "detects and synthesizes a real tool call" fallback when a model emits tool calls as
   text JSON, corroborated by the Cline/Roo-Code format-mismatch issues) — `unstuck-behavior`.
   This is the single most directly-applicable finding: it targets the *exact* known bug
   (model describes an edit in prose instead of calling `write_file`) with a mechanical,
   pre-`finish` fix rather than a post-hoc recovery turn. Concretely: if a turn's plain-
   text content contains something structurally shaped like a tool call (a JSON object
   with `name`/`arguments` keys, or a fenced code block that's clearly meant to be file
   content addressed to a specific path), attempt to parse and execute it as the real
   tool call before falling through to "no tool call, retry/recovery."

2. **A narrowly-scoped, single-purpose repair turn instead of a full-context retry**
   (source: Aider's architect/editor split — a second call given *only* the first
   model's own proposal, asked to do exactly one mechanical thing) — `unstuck-behavior`.
   When the harness detects a prose-only "I edited X" turn or an identical repeated
   call, the recovery ask should be maximally narrow ("here is what you just wrote;
   emit exactly one `write_file` call for it, nothing else") rather than replaying the
   whole task context, which is cheaper and gives the model less room to drift back into
   prose.

3. **A compact, structured "what's been tried" note carried across recovery/repeat
   attempts** (source: OpenHands' context condenser's goal/progress/remaining-work +
   preserved-files summary shape, and Albatross's `continue.md` handoff artifact) —
   `persistent-memory` (and secondarily `unstuck-behavior`, since its immediate use is
   breaking the identical-retry loop by making prior attempts visible to the model
   rather than only to the harness's failure counter). This doesn't require RAG or a
   vector store — a few lines injected into the next turn's prompt ("you already tried
   X at Ts; it failed because Y; don't repeat it verbatim") is enough to give the model
   the same signal `--repeated-command-failure-threshold` currently only uses to trigger
   a harness-side nudge after three failures, but *before* the third failure and in a
   form the model can act on directly.

Honorable mention not promoted to the top 3 but worth a line in
[[05-persistent-memory-decision]]: LocalHarness's zero-model-call, pure-SQL per-tool
success/failure history ("write gate") as a cheap way to flag a *repeat* of a
recently-failed exact call before it executes, rather than only detecting it after
three identical attempts.
