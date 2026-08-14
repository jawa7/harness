# Persistent-memory decision

Type: grilling
Status: open
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
