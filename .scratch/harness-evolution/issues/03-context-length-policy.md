# Context-length policy

Type: research
Status: claimed (research subagent dispatched)

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
