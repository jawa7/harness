# Context-length policy

Type: research
Status: resolved

## Question

What actually changes for `qwen2.5-coder-7b-instruct-mlx` (and the 3B
variant) between an 8k and a 32k context window in practice — quality
degradation curves ("lost in the middle"/context rot), latency and memory
cost on this hardware (MLX on Mac), and how much of the repo-context eval
cases' input actually needs to fit (measure token counts of the
`pyfixture-tasktracker` fixtures + a full `harness-log.md` transcript at
`--max-turns=16`). Conclude with a recommended context-length target (or
range) for `harness`'s default config, and whether it should vary by case
kind (isolated vs repo-context) or model size.

## Answer

Target 32K tokens (the model's native trained max — mlx-lm has no
out-of-box YaRN, so going higher isn't realistic on this stack) as the
server-side context window for both the 7B and 3B models; do not rely on
LM Studio's auto-detected default, which is documented to fall back to as
little as 2,048 tokens for MLX-converted models. Real `harness-log.json`
usage data shows repo-context transcripts reaching 5,000-11,000 tokens by
turn 16 and still climbing (not plateaued), while `qwen_agent.py` has no
token-aware trimming and no HTTP-error handling around the model call —
together these make an undersized/misconfigured context window a
plausible primary cause of the near-0% repo-context pass rate, not
something retry logic can fix. The 3B model's failures look more like
instruction-following collapse than context overflow, so widening its
window alone won't help it.

See [[../research/03-context-length-policy]] for full measurements and
sourcing.
