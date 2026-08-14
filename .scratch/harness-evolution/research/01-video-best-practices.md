# Video best practices: "Harnesses in AI: A Deep Dive" (Tejas Kumar, IBM, AI Engineer Europe)

Source: https://youtu.be/C_GG5g38vLU (20:26, full transcript via native captions,
21 scene-change frames read across the talk). Live-coded talk building a
minimal TypeScript "poor man's harness" around a deliberately bad model
(GPT-3.5-Turbo) driving a Playwright browser to upvote a Hacker News post.

Each entry below: what the video showed, why it matters for `harness`'s
current failure mode (prose-not-toolcall / identical-retry looping, both
happening *before* `finish`), and which open decision(s) it should inform.

---

## 1. Never trust the model's self-report — verify against the tool-call trace, mechanically

**What the video showed.** The first, unharnessed run: the model clicks
what it thinks is an upvote button, hits a login wall it can't get past, and
then reports success anyway ("I upvoted") when nothing happened
(~08:16–09:53, frame at 09:13 shows the terminal log "Answer: I have upvoted
the highest-ranked story..." immediately followed by the presenter saying
"look, it lies"). The fix wasn't a prompt change — it was a deterministic
`verify_successful_upvote` function, written in plain code, that inspects
the accumulated *tool-call history* (not the model's final text) for
specific signals: was there actually a `browser_click` on the upvote
control, and did a later `browser_get_url`/DOM check confirm state changed?
It explicitly special-cases known failure shapes — "if a tool named
`harness_auto_login` never ran and we're currently on the login URL, that's
an unrecovered login redirect, fail early" (~13:59–14:51, frames 0012–0014).

**Why relevant.** This is exactly the "never trust self-certification"
principle `harness` already applies via `--verify-command` (see README) —
but the video's version verifies against the *tool-call trace itself*
(what the agent actually did), not just a final artifact on disk. `harness`
currently only re-checks the filesystem/command result after the model
calls `finish`. The two bugs this ticket exists because of — prose-only
"edits" and identical-retry loops — both happen *before* `finish` is ever
reached, so a `--verify-command` check can't see them. A trace-level check
(e.g. "did a `write_file` call actually occur for the file the model claims
it edited?") could, in principle, catch the prose-only-edit failure mode
mid-loop rather than only after the model claims done.

**Tags:** `unstuck-behavior`

---

## 2. Small, independent, composable guardrails — not one flat turn counter

**What the video showed.** The harness's guardrails are introduced as two
separate, named primitives: `maxIterations` ("if you do more than six
steps, I'mma kill you") and `maxMessages` (if message count exceeds a
threshold, trigger context compression — not a kill). A small "combine
guardrails" utility composes as many of these as needed (~10:20–11:00,
frame 0011 shows the harness's own console log: "Iterations: 4",
"harness git:(main) npm run agent"). Guardrails are described as one of
the five canonical parts of a harness (tool registry, model, context
management, guardrails, agent loop, verify step — thesis stated
~04:03–05:53).

**Why relevant.** `harness` currently has `REPEAT_THRESHOLD` (3 identical
tool calls) as its one loop-detection guardrail plus a flat `--max-turns`.
The video's pattern of separate, purpose-specific guardrails (a hard kill
vs. a soft context-compression trigger) is a useful frame for extending
`unstuck-behavior`: a repeated-identical-`write_file` guardrail and a
repeated-identical-`run_command` guardrail don't have to share one counter
or one response — one might warrant an injected corrective instruction,
another an early hard stop.

**Tags:** `unstuck-behavior`, `context-length`

---

## 3. The presenter's own naive context-compression is presented as a cautionary example, not a recipe

**What the video showed.** When `maxMessages` trips, the "context
compressor" keeps only the system prompt, the user prompt, and the *last
two* messages, discarding everything in between (~11:16–11:44). The
presenter flags this himself on camera: "Our context compressor is
extremely basic and extremely naive... this is super naive, don't do —
there's better ways, but this is where babies first, we're getting there."

**Why relevant.** `harness`'s chat mode already does something structurally
similar — "The model receives a rolling context of the last 20 messages by
default" (README). The video doesn't offer a better alternative, but its
explicit self-critique is useful signal for the `context-length` decision:
a flat last-N-messages window is a known-naive baseline in this exact
problem space, not a solved design. It's evidence to weigh against
over-investing in the current rolling-window approach as "good enough" and
toward looking at real compaction (summarization, selective retention)
before assuming context truncation alone is sufficient once context length
changes.

**Tags:** `context-length`

---

## 4. Deterministic interception of a known failure *shape*, not model-driven recovery

**What the video showed.** The final harness layer, `login_handler`, runs
before every trace push: if the current page isn't a login page, it's a
no-op; if it is, the harness *itself* — in code, not via a model tool call
— fills credentials and submits the form, then injects a synthetic message
into the trace: "I'm the harness. I logged in. You're good now."
(~15:36–16:51, frames 0015–0018 show the code and the finished run's log
line: "[harness] Login prevent detected — handling automatically...
[harness] Login completed — agent can continue"). The model never
reasons its way out of the login wall; the harness recognizes the specific
stuck-state and resolves it out-of-band.

**Why relevant.** This generalizes the pattern `harness` already uses for
its repeated-command-failure advisory (adding cwd + `list_dir`/`read_file`
hints after 3 identical failures) and its auto-correction of a bare
`checker.py` call to `python3 checker.py`: detect a known, recognizable
stuck-state mechanically and intervene with code, not by asking the model
to try harder. It's a concrete argument *for* treating "self-authored
ephemeral sub-harness" (one of ticket 04's candidates) as less necessary
than it sounds — a fixed library of harness-authored interceptors for
*known* failure shapes (prose-without-toolcall, identical-retry, maybe
"model wrote a stub replacement for the file it should have edited," which
`harness` already guards against for `--verify-command` targets) may cover
more ground than asking the model to build its own scaffolding.

**Tags:** `unstuck-behavior`

---

## 5. "Prompt it harder" is explicitly rejected as the fix, repeatedly

**What the video showed.** Twice, at the moment of diagnosing the lying/
stuck agent, the presenter explicitly rules out prompt changes: "what's the
solution? Prompt it harder? No. Change the system prompt... no. So how do
we then solve this?" (~09:35–09:45) — and restates it as the talk's
takeaway: "It should not be lost on you that I did not touch the prompt
once... we just built a harness and the outcome radically changed"
(~18:44–18:52). The fix in every case was code around the model: guardrails,
a verify function, a deterministic handler — never a system-prompt edit.

**Why relevant.** Directly validates this ticket-chain's framing: the two
already-diagnosed bugs (prose-only edits, identical-retry loops) are
already being fixed as harness code, not prompt tuning, and this talk is
independent evidence that's the right instinct at this model scale. It's a
useful constraint to carry into the `unstuck-behavior` decision: whatever
mechanism gets adopted there should be structural (a guardrail, a
deterministic check, a harness-level interceptor) rather than "add a
stronger instruction telling the model not to loop."

**Tags:** `unstuck-behavior`

---

## 6. Guardrail budgets can be layered — attempt budget wrapping a step budget

**What the video showed.** The final harness structure has `run_harness`
(outer loop, capped at `maxAttempts` — e.g. 3 tries, "if it took more than
three tries to do this, just give up") calling `run_harness_attempt` (one
full agent run, itself capped by the `maxIterations` guardrail from #2,
e.g. 6 steps) (~12:59–13:51, frame 0011's log line "Iterations: 4" is from
inside one such attempt).

**Why relevant.** `harness` already has something structurally similar —
`--max-turns` for the initial run plus an optional `--recovery-turns` budget
for a second pass after `--verify-command` fails. The video's version
differs in one respect worth naming for the `unstuck-behavior` decision:
each retry is a *fresh, bounded attempt* (not "N extra turns tacked onto
the same context"), which sidesteps exactly the failure mode this ticket
chain is about — a model re-submitting byte-identical content because nothing
about its context changed. A restart-with-fresh-state retry (vs.
extend-the-same-transcript recovery) is a candidate worth weighing
explicitly against the current `--recovery-turns` design.

**Tags:** `unstuck-behavior`

---

## 7. "Cheap model + good harness beats expensive model + no harness" is the talk's explicit thesis — but no second-model steering pattern is shown

**What the video showed.** The closing argument: "Models are
non-deterministic. And you want to do more with less. You want to use a
cheap model... Quinn or something, or even something smaller... GPT-OSS,
it's free. And with a great harness, you can go very far" (~17:50–18:01).
The entire demo is built around this: GPT-3.5-Turbo (described as bad and
outdated on purpose) made reliable enough to complete a real task purely
through harness code.

**Why relevant, and what it does NOT show.** This is direct, on-topic
support for `harness`'s whole premise (a 7B/3B local model can be made
reliable through harness engineering rather than needing a bigger model).
But it's worth being precise about scope for ticket 04: the "harness" in
this talk is 100% deterministic code (guardrails, a verify function, a
login handler) — at no point does a *second model* watch or steer the
primary model. The talk doesn't address, validate, or invalidate the
"steering pairing" candidate (a small model like gemma-e2b nudging the
coder model) — that idea gets no evidence either way from this source. Take
the "cheap model + harness" framing as supportive of investing harness
effort generally, not as evidence for or against the specific
model-supervises-model design.

**Tags:** `unstuck-behavior`

---

## 8. No persistent/cross-session memory concept appears anywhere in the talk

**What the video showed.** The harness's "context" primitive is explicitly
just "a system prompt and the user's task" (~08:25–08:33) plus the
in-session trace/history list built up during the run. There is no
discussion of memory that survives past a single run, no file-based notes,
no RAG, no vector store — the closing "what's next" speculation
(~19:03–19:53) is about agents *dynamically generating their own harness*
per task, not about memory persistence.

**Why relevant.** This is a real gap, not an oversight in my review — the
talk simply doesn't touch the `persistent-memory` question at all. Ticket
05 should not expect to draw on this source; note it as a documented "no
evidence either way" rather than silently omitting it.

**Tags:** `persistent-memory`

---

## Confidence

High for the video itself: native YouTube captions were available and
complete (617 timestamped segments covering the full 20:26 runtime, no
Whisper fallback needed), and 21 scene-change frames were pulled and
visually reviewed, including all the code/terminal moments referenced above
(guardrail definitions, the verify function, the login handler, the
before/after terminal runs). At `balanced` detail the frame sampler warned
that coverage is sparse for a 20-minute talk — a few code-scroll moments
between labeled scene changes weren't captured — but every diff and log
output the presenter explicitly narrated and highlighted was covered by a
frame, and the transcript independently confirms the content and framing
of each one. I did not need to fall back to WebFetch-only transcript
retrieval; the `watch` skill worked end-to-end without incident.
