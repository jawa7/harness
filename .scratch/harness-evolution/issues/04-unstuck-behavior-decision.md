# Unstuck-behavior decision

Type: grilling
Status: claimed
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
