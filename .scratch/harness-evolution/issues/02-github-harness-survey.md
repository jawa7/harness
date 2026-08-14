# GitHub harness survey

Type: research
Status: claimed (research subagent dispatched)

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
