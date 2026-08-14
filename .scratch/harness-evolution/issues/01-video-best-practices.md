# Video best practices

Type: research
Status: resolved

## Question

Watch https://youtu.be/C_GG5g38vLU (via the `/watch` skill) and extract any
best practices that apply to `harness`: agent-loop structure, tool-call
design, stuck/recovery handling, context management, model pairing/steering,
or anything else concretely actionable for a small local-model coding-agent
harness. Produce a list of candidate practices, each tagged with which open
decision it feeds ([[04-unstuck-behavior-decision]],
[[03-context-length-policy]], or [[05-persistent-memory-decision]]), and note
anything that doesn't apply to a single-machine, MLX/local-model setup.

## Answer

Watched the full 20:26 talk ("Harnesses in AI: A Deep Dive," Tejas Kumar/IBM)
via native captions plus 21 reviewed frames — no fallback needed. Its core
thesis — reliability comes from deterministic harness code (composable
guardrails, trace-based verification instead of trusting the model's final
report, special-case interceptors for known failure shapes) rather than from
prompting harder or swapping in a bigger model — directly supports and
extends `harness`'s existing never-trust-self-certification design: verify
against the tool-call *trace* (not just final output) to catch prose-only
edits and identical-retry loops before `finish` is reached, and prefer
fresh-attempt retries over extending an already-stuck transcript. The talk
offers no evidence on model-supervises-model steering or on persistent
cross-session memory — both are flagged as explicit gaps in the write-up
rather than silently skipped. Full write-up:
[../research/01-video-best-practices.md](../research/01-video-best-practices.md).
