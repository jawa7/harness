# GitHub harness survey

Type: research
Status: resolved

## Question

Survey existing open-source harnesses/agent frameworks aimed at improving
tool-use and task-completion reliability for *small local coding models*
(7B-class and below; MLX, llama.cpp, Ollama-served, etc.) — not generic
cloud-LLM agent frameworks. For each relevant project found, capture: what
problem it solves, how it handles a model getting stuck (retry loops,
self-decomposition, escalation to a second model, sandboxed sub-harnesses),
how it manages context/memory, and whether it's a fit to adopt wholesale,
borrow specific practices/code from, or skip. Feed findings into
[[04-unstuck-behavior-decision]] and [[05-persistent-memory-decision]].

## Answer

Surveyed 7 projects (SWE-agent, Aider, OpenHands, moatless-tools/tree-search,
Albatross, LocalHarness) plus supporting evidence (Cline/Roo-Code local-model
tool-call issue threads, an NVIDIA SLM-agents paper) — none is a wholesale
fit, but three concrete practices are directly transferable: (1) a
text/JSON-shaped tool-call synthesis fallback (Albatross) that targets
harness's exact known bug of prose-only fake edits, tagged
`unstuck-behavior`; (2) a narrowly-scoped single-purpose repair turn instead
of a full-context retry (Aider's architect/editor split), tagged
`unstuck-behavior`; and (3) a compact structured "what's been tried"
progress note carried across recovery attempts (OpenHands' condenser shape /
Albatross's `continue.md`), tagged `persistent-memory` and secondarily
`unstuck-behavior`. Full findings, sources, and per-project assessments:
[../research/02-github-harness-survey.md](../research/02-github-harness-survey.md).
