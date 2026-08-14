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
- The prose-only-edit and identical-retry-loop bugs above are being fixed as
  a separate immediate task outside this map (decided in Q4) — don't
  re-litigate them in [[04-unstuck-behavior-decision]]; that ticket is about
  additional/complementary unstuck mechanisms beyond that bugfix.
- Skills every session should consult: `/watch` for
  [[01-video-best-practices]]; `/research` subagent for
  [[01-video-best-practices]], [[02-github-harness-survey]],
  [[03-context-length-policy]]; `/grilling` + `/domain-modeling` for
  [[04-unstuck-behavior-decision]], [[05-persistent-memory-decision]].
- This map's destination is a spec only (Q6) — no implementation tickets
  belong here; a follow-on execution effort/map consumes the finished spec.

## Decisions so far

## Not yet specified

- Exact implementation architecture for whichever unstuck-mechanism(s)
  [[04-unstuck-behavior-decision]] selects — deferred to a follow-on "build"
  effort once this map's spec is locked.
- Final priority/sequencing of which improvements ship first — resolved in
  [[06-assemble-design-spec]] once 03–05 are answered.

## Out of scope
