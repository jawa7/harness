# Persistent-memory decision

Type: grilling
Status: resolved
Blocked by: 02, 03

## Question

Does `harness` need cross-session/cross-task persistent memory at all, and
if so, what shape — a real RAG pipeline (embeddings + vector store) or a
lighter mechanism (flat file/log the model can grep, structured notes,
something else)? Ground this in what [[02-github-harness-survey]] found
other small-local-model harnesses doing for memory, and in
[[03-context-length-policy]]'s conclusion about how much fits in-context
without external memory at all — a wider context window may reduce or
remove the need for RAG. Resolve to a specific choice (including "no memory
needed yet") with justification.

## Answer

Three separate axes, each resolved differently — the original question
conflated them:

**1. Cross-eval-sample / cross-task memory: explicitly not in scope, at all.**
`run_once`'s samples are deliberately isolated (each in its own workdir) —
that isolation is the basis of `evals/python-coder`'s N-samples-per-case
comparability. Sharing any memory between samples or between unrelated
tasks would contaminate that. Confirmed out of scope by this session — not
"low priority," genuinely not what this ticket's persistent-memory question
was ever asking about.

**2. Same-repo, cross-session continuity (real, non-eval usage): add a
lightweight handoff artifact.** `qwen_agent.py` already has
`save_chat_session`/`load_chat_session` — the full transcript persists
across chat-mode sessions today. On top of that, add a compact, structured
handoff note in the Albatross `continue.md` / LocalHarness capped-summary
shape (done / in-progress / key decisions / next steps / key files),
written at session end and read back in at the next session's start on the
same repo. This is small, no new infrastructure (no embeddings, no vector
store, no second model), and both surveyed precedents ship it in production
harnesses. Not RAG — a note, not a retrieval index.

**3. Big real repos that don't fit in 32K context: repo-map/file-tree
navigation first, embeddings-based RAG only as a later upgrade path, gated
behind a concrete trigger.** This is a different problem from #2 (retrieval
*within* one large repo at a point in time, not continuity *across* time)
and from ticket 04's in-run plan/tried-state (which tracks progress on the
current task, not repo content). Zero of the 6 surveyed harnesses use
embeddings/vector-store RAG for this problem class; the two credible,
actively-maintained precedents (Aider's static token-budgeted repo map,
moatless-tools' iterative file-tree navigation) solve it without embeddings
— cheaper to build (no embedding model, no vector DB, no
reindex-on-file-change staleness problem) and already proven at real scale
(Aider: 48K★, actively maintained). Spec this as the first-line mechanism.
Full embeddings RAG stays a documented upgrade path, not built now — gate
it behind a concrete, measurable trigger (e.g., a repo whose repo-map
itself doesn't fit in a reasonable context budget), not spec'd as day-one
work, since no current harness usage has hit that ceiling.
