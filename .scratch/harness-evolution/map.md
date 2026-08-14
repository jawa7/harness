# Map: harness evolution

## Destination

A locked, prioritized design spec (single markdown doc in this repo) for
evolving `harness` so it stops getting stuck on repo-context coding tasks —
covering: (1) the concrete unstuck-behavior mechanism(s) to adopt, informed
by a specific YouTube video and a survey of existing OSS harnesses for small
local coding models, (2) the harness's context-length policy, and (3)
whether/how to add persistent memory (RAG or something lighter). This map
produces the spec only — building it is a separate, later effort.

## Notes

- Repo: `~/harness` (migrated from `~/.claude/scripts/qwen-harness`,
  now https://github.com/jawa7/harness, private). Migration and GitHub
  remote creation were handled as one-off actions outside this map, not
  tickets.
- Domain: local-model coding-agent harness (`qwen_agent.py`), MLX-based
  models (`qwen2.5-coder-7b-instruct-mlx`, `-3b-`), evaluated via the
  `evals/python-coder` suite — pytest-verified pass/fail, N samples per case,
  isolated vs repo-context case kinds.
- Already-established input fact for this map: comparing the
  `loc-25-clean-20260813T162231` and `loc-25-recovery2-20260813T164655` eval
  runs (same model, only `--recovery-turns` toggled 0→2) showed overall
  38%→46%, isolated 53%→73%, but repo-context stuck at 11%→0%. Root cause on
  repo-context cases: the model either describes an edit in prose without
  emitting the `write_file` tool call, or repeats an identical
  `run_command`/`write_file` verbatim — both loop *before* reaching `finish`,
  so the `--recovery-turns` repair budget (which only fires after a failed
  `finish`) structurally cannot reach them.
- Three bugs are being fixed as a separate immediate task outside this map
  (decided in Q4) — don't re-litigate them in
  [[04-unstuck-behavior-decision]]; that ticket is about
  additional/complementary unstuck mechanisms beyond this bugfix task:
  1. prose-only fake edits (model describes an edit, never calls `write_file`)
  2. identical-retry loop (verbatim repeated `run_command`/`write_file`)
  3. `summarize.py`'s `classify_failure` mislabels an outright API crash
     (e.g. HTTP 400 before turn 1) as `prose-only`, masking real failures
     under a model-behavior category — found while resolving
     [[03-context-length-policy]], see Decisions so far.
- Skills every session should consult: `/watch` for
  [[01-video-best-practices]]; `/research` subagent for
  [[01-video-best-practices]], [[02-github-harness-survey]],
  [[03-context-length-policy]]; `/grilling` + `/domain-modeling` for
  [[04-unstuck-behavior-decision]], [[05-persistent-memory-decision]].
- This map's destination is a spec only (Q6) — no implementation tickets
  belong here; a follow-on execution effort/map consumes the finished spec.

## Decisions so far

- [Video best practices](issues/01-video-best-practices.md) — 8 practices from "Harnesses in AI: A Deep Dive" (Tejas Kumar), high confidence (full transcript + frames). Headline: verify against the tool-call *trace*, not the model's self-report; deterministic interception of known failure shapes beats "prompt it harder"; layered attempt-budget + step-budget retries. No model-supervises-model or persistent-memory pattern shown in the video. See [findings](research/01-video-best-practices.md).
- [GitHub harness survey](issues/02-github-harness-survey.md) — 7 sources, repos verified live via `gh api`. Top hit: **Albatross** (github.com/morganlinton/Albatross, 219★, active, local-model-first incl. MLX) detects prose/text-JSON tool calls and synthesizes a real call — directly targets our fake-prose-edit bug. Aider's architect/editor split and OpenHands's context condenser suggest a narrow single-purpose repair turn + a "what's already been tried" handoff note, instead of RAG, to break the identical-retry loop. See [findings](research/02-github-harness-survey.md).
- [Context-length policy](issues/03-context-length-policy.md) — recommend 32K target (Qwen2.5-Coder's native trained max; mlx-lm has no YaRN, so higher isn't real). `qwen_agent.py` currently has **no context-window config at all** — it lives invisibly in the external LM Studio server, which is documented to silently fall back to as little as 2,048 tokens for MLX models it can't read metadata for. Confirmed via real `usage.prompt_tokens` across 126 runs: repo-context transcripts hit 5-11K tokens by turn 16 and are still climbing. Cites Liu et al. 2023 (Lost in the Middle), NVIDIA RULER, Qwen2.5-Coder Technical Report. See [findings](research/03-context-length-policy.md).
- **New bug surfaced during 03's research, not previously known**: the `loc-25-3b-20260813T140000Z` run (3B model) shows all 24/24 samples `crashed: "HTTPError: HTTP Error 400: Bad Request"` with `turns: 0` — the very first API call failed, before the model ever got to respond. `summarize.py`'s `classify_failure` has no category for "crashed before any turn" and silently buckets it as `prose-only` (its fallback when `tool_calls` is empty), so the comparison report shows "0% pass, all prose-only" instead of surfacing an outright request failure. This is a harness/tooling bug (miscategorization), not a model-behavior bug — added to the separate outside-map bugfix task alongside the fake-prose-edit and identical-retry-loop bugs (see Notes).

## Not yet specified

- Exact implementation architecture for whichever unstuck-mechanism(s)
  [[04-unstuck-behavior-decision]] selects — deferred to a follow-on "build"
  effort once this map's spec is locked.
- Final priority/sequencing of which improvements ship first — resolved in
  [[06-assemble-design-spec]] once 03–05 are answered.

## Out of scope
