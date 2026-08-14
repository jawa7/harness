# Assemble design spec

Type: task
Status: resolved
Blocked by: 03, 04, 05

## Question

Write the destination artifact: a single prioritized design spec
(`DESIGN.md` at the repo root, or similar) that assembles the decisions from
[[03-context-length-policy]], [[04-unstuck-behavior-decision]], and
[[05-persistent-memory-decision]] into one document — what to build, in what
order, and why, with enough detail that a follow-on execution effort can
plan directly from it without re-deriving the reasoning. No code changes in
this ticket; the spec is the deliverable.

## Answer

Written to `DESIGN.md` at the repo root. Five tiers, in build order: Tier 0
(context-window config + `call_model()` error handling — cheap, unblocks
accurate measurement of everything after it), Tier 1 (the three unstuck
mechanisms from 04 — the direct fix for the 0% repo-context pass rate),
Tier 2 (token-aware context trimming, supports Tier 1's larger
transcripts), Tier 3 (same-repo handoff artifact + repo-map navigation from
05), Tier 4 (explicitly deferred/gated: pluggable second-opinion model,
self-authored-harness prompt tweak, full embeddings RAG — none built
without a measured residual gap or a concrete trigger). Cross-references
every claim back to its source ticket/research file rather than restating
the reasoning inline.
