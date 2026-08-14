# Context-length policy

Research for [[../issues/03-context-length-policy]].

## Method

Part A uses two sources, in order of trust:

1. **Real token counts recorded by the model server itself**, in
   `evals/python-coder/runs/**/harness-log.json` (`usage.prompt_tokens` /
   `usage.completion_tokens` per turn, from the OpenAI-compatible
   `/chat/completions` response). This is ground truth, not an estimate —
   126 harness-log.json files were parsed with a small Python script.
2. **Word-count estimation** (~1.3 tokens/word for code, the standard rule
   of thumb) only for artifacts that have no recorded usage — the raw
   fixture source files — as a sanity check against (1). No tokenizer
   library was available in this environment, but real usage data made that
   mostly unnecessary.

Part B is web research against primary sources (model cards, technical
reports, papers, framework docs/discussions), cited inline.

## Part A — this repo's actual token footprint

### A1. The seeded repo tree is small; the *transcript* is what grows

`evals/python-coder/fixtures/pyfixture-tasktracker/base/` (the repo-context
fixture's starting state) is 9 files, **470 words total** (~610 estimated
tokens): `service.py` (127w), `storage.py` (124w), `models.py` (55w),
`tests/test_service.py` (51w), `tests/test_storage.py` (48w), `cli.py`
(26w), `tests/test_cli.py` (19w), `errors.py` (14w), `logging_utils.py`
(6w). Each repo-context case's `overlay/` adds one or two more small files
(e.g. `fix-bug-with-context/overlay/storage.py` 132w +
`tests/test_update_context.py` 91w). The system prompt
(`evals/python-coder/system.txt`) is 106 words; a task file is ~40-50
words. Total raw content a model would need to read once through is well
under 1,000 tokens.

This matters: **the repo itself was never the problem.** The real
`prompt_tokens` recorded at turn 1 for repo-context cases is 1,464-1,509 —
already ~2.5x the raw-content estimate, because the request also carries
the full tool-calling JSON schema for every enabled tool (`write_file`,
`read_file`, `list_dir`, `run_command`, `finish`) plus (when enabled) the
`--bootstrap-workspace-context` inventory. That fixed per-request overhead
is small compared to what turns 2-16 add.

### A2. Real per-turn growth, from actual `harness-log.json` usage data

Across 126 harness-log.json files (isolated + repo-context, both models),
turn-by-turn `prompt_tokens` for the three largest examples found
(chosen because they show the two dominant growth patterns):

**Repo-context, `follow-existing-pattern`, sample-2, 16 turns, still climbing at cutoff:**
```
turn 1: 1509   turn 5: 2812   turn 9: 5787   turn 13: 8891
turn 2: 1669   turn 6: 3743   turn 10: 7088  turn 14: 9156
turn 3: 2093   turn 7: 5040   turn 11: 7247  turn 15: 9571
turn 4: 2354   turn 8: 5325   turn 12: 7470  turn 16: 10985
```
Delta between turn 15 and 16 is +1,414 tokens — the curve has not
plateaued; it would keep climbing well past 16 turns if allowed to.

**Repo-context, `fix-bug-with-context`, sample-3, 16 turns:** 1,464 → 10,145,
a similar near-linear climb (~580 tokens/turn average, front-loaded by two
large jumps at turns 4-6 when file contents are first read into context).

**Isolated, `add-cli-flag`, sample-1, 16 turns:** 984 → 6,460, almost
perfectly linear (+352 tokens/turn) because the model is repeating a
near-identical 286-completion-token turn 16 times without ever calling
`finish` — a stuck loop, not new information, but the transcript still
grows because nothing is summarized or discarded within the turn budget.

Aggregated over every harness-log.json with recorded usage: **repo-context
final-turn `prompt_tokens` averages 5,428 (max 10,985, n=23)**;
**isolated final-turn `prompt_tokens` averages far lower** (the isolated
population includes many short 1-2 turn runs that ended in `prose-only`
failures before accumulating much history, so the mean is pulled down, but
the isolated cases that ran a full 16 turns — `add-cli-flag`,
`fix-off-by-one-bug` — reached 6,460 and 7,689 respectively, comparable to
the repo-context tail).

### A3. `qwen_agent.py`'s actual context configuration — and what's missing

Grepped `qwen_agent.py` directly for `context`, `n_ctx`, `max_tokens`,
`--max-turns`:

- **`--max-tokens`** (default 8000, `qwen_agent.py:2029`): this is the
  **per-response completion cap** sent as `max_tokens` in the
  `/chat/completions` payload (`call_model()`, line 826-834) — it bounds
  how many tokens the model may *generate* in one turn, not the size of
  the input context window.
- **`--max-turns`** (default 10 for the CLI, `DEFAULT_CHAT_MAX_TURNS = 10`
  for chat mode, `evals.py` defaults to 12; the python-coder comparison
  suite explicitly overrides this to **`--max-turns=16`**, confirmed in
  `evals/python-coder/runs/loc-25-3b-20260813T140000Z/comparison-manifest.json`
  and `.../loc-25-preflight-blocked-20260813T141444Z/comparison-manifest.json`,
  both listing `"--max-turns=16"` in `harness_flags`).
- **`context_messages`** (`DEFAULT_CHAT_CONTEXT_MESSAGES = 20`): a
  **message-count** rolling window, not a token budget. `trim_chat_messages()`
  (line 878) groups the transcript into user-turn blocks and keeps the most
  recent ones under a `max_messages` cap, with special handling so a
  tool-call/tool-result pair is never split. Nowhere does this function (or
  anything else in the file) look at token counts — a single turn with a
  large file read can be many hundred tokens and still count as "one
  message" against the 20-message budget.
- **There is no `n_ctx`, `context_length`, `context_window`, or any
  server-side context-size setting anywhere in `qwen_agent.py`.** The
  README confirms this is by design: the harness "*Requires an LM Studio
  (or other OpenAI-compatible) server already running with the model
  loaded*" — the actual context window is a property of that already-running
  server/model load, entirely outside this repo's code or CLI surface.
  `qwen_agent.py` cannot see it, request it, or react to it.
- **`call_model()` has no exception handling.** It calls
  `urllib.request.urlopen()` with no `try/except` around it (line 841); an
  HTTP error response (e.g., the canonical "this model's maximum context
  length is X tokens" 400 that OpenAI-compatible servers — LM Studio,
  llama.cpp, vLLM — return when a request's tokens exceed the loaded
  context) is an **unhandled `HTTPError` that crashes the run**, not a
  recoverable condition the harness can retry or diagnose.

### A4. Direct evidence this already happened

`evals/python-coder/runs/loc-25-3b-20260813T140000Z/comparison-report.json`
(a `--max-turns=16`, 3B-model run, 24 samples across all 8 cases, isolated
and repo-context alike) recorded:

```
"passed_samples": 0, "failed_samples": 24,
"collection_failures": {
  "HTTP 400 Bad Request": 22,
  "model stopped without finish then verifier failure": 2
}
```

Pass rate was **0% on both isolated and repo-context slices** for this run.
Spot-checking one individual sample (`implement-lru-cache` sample-1) shows
a different, non-context failure mode for that specific case (the 3B model
answered in prose without ever calling a tool, `finish_reason: "stop"`,
1 turn, 1,267 prompt tokens — a pure instruction-following failure, not a
context issue). But the *aggregate* 22/24 "HTTP 400 Bad Request" figure at
the manifest level is consistent with the context-window-exceeded failure
mode described in A3: given the harness has no HTTP error handling and no
token-aware trimming, and given A2 shows conversations routinely reach
6,000-11,000 tokens by turn 16, a server loaded with a small or default
context window would produce exactly this signature — a burst of HTTP 400s
partway through a long run. This is circumstantial, not proven (no server
logs were captured), but it lines up with everything else in this report
and is worth the harness authors confirming directly against their LM
Studio context-length setting.

## Part B — what changes between 8k and 32k context in practice

### B1. Qwen2.5-Coder's actual context envelope

The Qwen2.5-Coder Technical Report (Hui, Yang et al., arXiv:2409.12186)
and the official model cards
([Qwen/Qwen2.5-Coder-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct))
state the model's **native trained context is 32,768 tokens**
(`max_position_embeddings` in `config.json`). Long-context support up to
**128K tokens is available only by explicitly enabling YaRN** RoPE scaling
(`rope_scaling` with `factor: 4.0`, `original_max_position_embeddings:
32768`) — this is opt-in, off by default, and the model card itself frames
it as extrapolation for long documents/repos, evaluated with a custom
"Needle in the Code" task (a function inserted at a random position in a
repo, tested for recall) rather than the standard RULER suite.

Critically for this stack: **mlx-lm does not support YaRN out of the box.**
A maintainer thread on the MLX repo
([ml-explore/mlx discussion #1808](https://github.com/ml-explore/mlx/discussions/1808))
confirms YaRN scaling for Qwen models requires hand-editing the model
config and porting the RoPE implementation from another supported
architecture — not something the `qwen2.5-coder-7b-instruct-mlx` /
`-3b-` quantized models this harness targets support automatically. **In
practice, on this stack, 32,768 tokens is the real ceiling**, not 128K.

### B2. "Lost in the middle" / context rot is real and well-documented

Liu et al., 2023, *"Lost in the Middle: How Language Models Use Long
Contexts"* (arXiv:2307.03172) is the primary source: across
key-value-retrieval and multi-document QA tasks, every model tested showed
a **U-shaped accuracy curve** — best when the relevant information is at
the very start or very end of the context, and measurably worse when it's
buried in the middle, regardless of the context window's nominal capacity.

RULER (NVIDIA, Hsieh et al., arXiv:2404.06654, "*What's the Real Context
Size of Your Long-Context Language Models?*") extends this: on
retrieval-plus-reasoning tasks (not just simple needle-in-haystack), **all
models tested showed large performance degradation as input length grew,
and none maintained quality at their claimed context length** except one
outlier model at 2x its claimed window. Passing a simple
needle-in-a-haystack test is not evidence a model reasons well over the
same-length input for a harder task — exactly the situation for a coding
agent that must track multiple files, prior tool outputs, and an evolving
plan simultaneously, which is a much harder task than single-fact
retrieval.

Neither paper benchmarks Qwen2.5-Coder-7B/3B specifically, but both are
general, primary-source evidence that **"the model advertises 32K" is not
the same claim as "the model reasons reliably at 32K,"** and that a 7B (and
especially a 3B) model — much smaller than anything tested in RULER's
headline results — should be assumed to degrade well before its nominal
window, not at it.

### B3. MLX / Apple Silicon inference and memory cost of longer context

From an MLX systematic benchmark
([ml-explore/mlx discussion #3209](https://github.com/ml-explore/mlx/discussions/3209),
5 models × 6 quantizations × 7 context lengths on M3 Ultra) and a
follow-up survey (arXiv:2511.05502, "Production-Grade Local LLM Inference
on Apple Silicon"):

- **The KV cache, not model weights, dominates memory/bandwidth at long
  context.** The KV cache is stored/computed in FP16 regardless of how
  aggressively the weights are quantized, so the benefit of int4-quantized
  weights *shrinks* as context grows — at 128K context, ~70%+ of memory
  bandwidth is spent reading the KV cache, and a heavily-quantized model's
  throughput advantage over full-precision nearly disappears (a Q2 model
  at 128K was only 1.7x faster than F16, versus 4.6x faster at 1K context).
- **Throughput drops substantially as context grows even for 7B-class
  models.** A Mixtral 8x7B example in the same benchmark: 68.4 tok/s at 1K
  context → 46.7 tok/s at 32K context (~32% slower for a 32x longer
  context) at Q4.
- **Prefill (prompt processing) time grows faster than decode time** due to
  quadratic attention cost — the same source reports up to a 55% prefill
  throughput reduction between 1K and 128K tokens. For an agent loop that
  resends the *entire* growing transcript every turn (as `qwen_agent.py`'s
  `build_context()` does — see A3), this means **prefill cost, not
  generation cost, grows every single turn**, compounding with turn count.
- KV-head count (GQA config), not raw parameter count, is "the critical
  parameter" for how much a longer context costs in memory — Qwen2.5-Coder-7B's
  4 KV heads (28 attention heads, GQA) is a relatively cache-cheap
  configuration, which somewhat softens the memory-cost argument against a
  32K target, but does not change the throughput/quality tradeoffs above.

### B4. LM Studio's undocumented default is a live risk for this harness

A practical, harness-relevant finding: LM Studio (the server this harness
is built against — see README, "Requires an LM Studio... server already
running") determines context length from the model's `max_position_embeddings`
metadata **except that, for MLX-converted models specifically, it has been
reported to fall back to as little as 2,048 tokens when it cannot
confidently detect the model's real capability**
([localllm.in](https://localllm.in/blog/lm-studio-increase-context-length)).
Combined with A3 (this harness has zero visibility into or control over
that setting) and A2 (real conversations reach 6-11K tokens by turn 16),
**a silently-undersized LM Studio context setting is a plausible, cheap
explanation for exactly the failure signature seen in A4** (a burst of
HTTP 400s partway through a `--max-turns=16` run). This should be treated
as a configuration checklist item, not just a modeling question.

## Conclusion and recommendation

**Target: 32,768 tokens (32K) as the default context-window configuration
for the LM Studio / MLX server this harness talks to, for both the 7B and
3B models, with `--max-turns` and `context_messages` tuned to leave real
headroom under it rather than up against it.**

Reasoning:

- 32K is Qwen2.5-Coder-7B/3B's **native trained maximum** — the point past
  which the model's own YaRN extrapolation would be needed, and mlx-lm
  doesn't support YaRN without manual config surgery (B1). Targeting more
  than 32K on this exact stack (MLX server + these quantized models) is not
  presently a realistic option, so it isn't a live choice — 32K is close to
  the practical ceiling, not an arbitrary middle ground.
- It is comfortably above every real transcript size measured in this repo
  (max observed: 10,985 tokens at turn 16, still climbing) — so 32K buys
  real headroom rather than being immediately re-exhausted at slightly
  higher turn counts, which the harness will likely want (repo-context
  cases had not plateaued by turn 16; a fix for the near-0% repo-context
  pass rate may well involve *more* turns, not fewer).
- 8K is demonstrably too tight: several *isolated* single-file cases alone
  (`add-cli-flag`, `fix-off-by-one-bug`) already crossed 6-8K tokens by
  turn 16 with no repo context at all, purely from resending the growing
  transcript every turn. An 8K window would start truncating/erroring
  isolated cases too, not just repo-context ones.

**Should it differ by case kind?** Not the window size itself — 32K is a
reasonable single target for both kinds on this hardware/model
combination. But the *effective* difference should show up in how much of
that budget gets used: repo-context cases are the ones actually pushing
toward the edge (average final-turn usage 5,428 vs. isolated's much lower
average, and repo-context is the population that hadn't plateaued by turn
16 — A2). If a token-based ceiling has to be enforced pragmatically before
32K is confirmed configured server-side, repo-context cases should get the
larger allowance of the two.

**Should it differ by model size?** The context *window* itself does
not need to differ — both the 7B and 3B are the same architecture family
with the same native 32K limit. But B2's general finding (smaller models
degrade earlier relative to fill fraction) plus this repo's own evidence
(the 3B model's dominant failure mode in the sampled data was
`no_tool_prose` / instruction-following collapse, often within the first
1-2 turns, well before context was the bottleneck — A4) suggests the 3B
model's problem is *not* primarily context length. Widening its context
window will not fix a model that answers in prose without calling a tool
turn 1. The 3B model likely needs a tighter turn budget and probably more
aggressive prompting/scaffolding rather than more context.

**Is the current context/turn setup already a bottleneck for repo-context
specifically?** Yes, very likely one of the primary bottlenecks, for two
independent reasons found in this repo's own data:

1. Repo-context transcripts are the ones that grow largest and were still
   climbing (not plateaued) at the `--max-turns=16` cutoff used in the
   comparison suite (A2) — meaning some fraction of repo-context failures
   may simply be "ran out of turns/context before finishing," which no
   amount of retry-logic or recovery-turns will fix if the ceiling itself
   is the constraint.
2. `qwen_agent.py` has no token-aware trimming (only message-count
   trimming, A3) and no HTTP-error handling in `call_model()` — so if the
   server's actual configured context window is anywhere near LM Studio's
   documented MLX fallback default of ~2,048 tokens (B4), a
   `--max-turns=16` repo-context run reaching 5,000-11,000 real tokens by
   the end would not degrade gracefully — it would crash with an unhandled
   HTTP 400, exactly the failure signature already observed in this repo's
   own `loc-25-3b` run (A4).

**Concrete next steps this points to**, independent of picking a number:

- Explicitly set/verify the LM Studio (or other server) context-length
  setting to 32K when loading `qwen2.5-coder-7b-instruct-mlx` and
  `-3b-instruct-mlx` — do not rely on auto-detection, given B4's documented
  MLX fallback risk.
- Add a `try/except` around `call_model()`'s `urlopen()` call so a
  context-length-exceeded (or any) HTTP error becomes a diagnosable,
  loggable harness outcome instead of an unhandled crash — this alone would
  turn ambiguous "prose-only"/"HTTP 400" collection failures into a
  distinguishable, countable failure category.
- Consider making `trim_chat_messages()` token-aware (or token-aware in
  addition to message-count-aware), since a single large `read_file` result
  can already exceed a meaningful fraction of an 8K budget and the current
  20-message window provides no protection against that.
