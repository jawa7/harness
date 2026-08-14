# qwen-harness

Minimal stdlib-only agent loop for local LM Studio models with tool calling
(`write_file`, `read_file`, `list_dir`, `finish`, and optionally
`run_command`). File writes are sandboxed to `--workdir`; irreversible
commands go through a propose/confirm/execute gate instead of running
directly (see below).

```
python3 qwen_agent.py \
  --workdir /path/to/project/some-output-dir \
  --system system-prompt.txt \
  --task task-prompt.txt \
  --max-turns 10 \
  --model qwen/qwen2.5-coder-14b \
  --base-url http://localhost:1234/v1
```

Requires an LM Studio (or other OpenAI-compatible) server already running
with the model loaded. Writes `<log-basename>.json` and `<log-basename>.md`
into `--workdir` on every run — full transcript (including per-turn token
usage), not just the final artifact. `log-basename` defaults to
`harness-log`, with a suffix auto-derived from `--task`'s filename
(`task.txt` -> `harness-log`, `task-b.txt` -> `harness-log-b`), or set
`--log-prefix` explicitly. This matters as soon as more than one variant
shares a flat `--workdir` — without a distinct suffix per run, the second
run silently overwrites the first run's log, no error. (Found the hard way:
Variant B's run clobbered Variant A's `harness-log.json` in
`fame5-bible-v2-prototype` before this existed; recovered only because it
happened to already be committed to git.)

## Opt-in coding workspace context

Coding runs can send a metadata-only inventory to the model before its first
action:

```sh
python3 qwen_agent.py ... --bootstrap-workspace-context \
  --bootstrap-inventory-limit 200 --bootstrap-include-verify-command
```

The inventory is sorted, bounded by the configured number of inspected entries,
uses paths relative to `--workdir`, and excludes every symlink rather than
following it. It never includes file contents. The optional verification advisory
is labelled read-only harness configuration; enable it only for a command that
contains no secrets. These flags are off by default. When the exact same
`run_command` fails repeatedly, the harness adds the actual cwd and a
`list_dir`/`read_file` next step after three failures (adjust with
`--repeated-command-failure-threshold`).

## Interactive chat sessions

The original one-shot CLI above remains unchanged. For a persistent terminal
chat, run:

```
python3 qwen_agent.py chat
```

The chat setup asks whether to create or resume a session. For a new session
it interactively selects a model from the server's `/v1/models` endpoint (or
accepts a manually entered model ID), working directory, system prompt, tool
set, and per-message turn limit. The server URL can be changed when creating
the session:

```
python3 qwen_agent.py chat --base-url http://localhost:1234/v1
```

Only the tools selected during setup are sent to the model. `run_command`
also asks for an allowlist of binaries; it cannot be combined with web tools.
Web tools are shown only when `TAVILY_API_KEY` is configured. The `finish`
tool remains internal and ends only the current request, not the terminal
chat. When your message includes a URL, the chat enforces `web_fetch` for
that URL before allowing `web_search`. A successful fetch closes web browsing
for that request, so the agent summarizes the supplied page instead of finding
unrelated sources. If a web operation fails after a source has succeeded, the
agent must answer from the material already collected and state the limitation.

When `TAVILY_API_KEY` is configured, choose **save or print previously fetched
pages** during new-session setup to also enable `web_fetch` and a persistent
page cache. Each successful `web_fetch` records an
immutable timestamped snapshot in the session folder: exact raw response bytes,
the complete extracted text, URL, content type, timestamp, and a `fetch_id`.
The model still receives only an 8,000-character excerpt, but it can use these
cache-only tools for lossless output without manually copying page content:

```
save_fetched_page({"url": "https://example.com", "path": "page", "format": "both"})
print_fetched_page({"url": "https://example.com", "format": "text"})
```

`both` writes `page.raw` (the exact bytes) and `page.txt` (full extracted text)
in the session workdir. With a single format, `path` is used literally. The
print tool writes the entire cached content directly to the terminal, bypassing
the model token limit; raw terminal output is decoded with the response charset.
Both tools work only from a cached `web_fetch` snapshot and never fetch the
internet themselves. `fetch_id` may select an older snapshot; otherwise the
latest snapshot for that URL is used. Snapshots cap raw downloads at 2 MB and
report when that cap was reached.

Chat permits at most 12 `web_search`/`web_fetch` network requests for one user
message and search queries are limited to 300 characters. With no successful
web source, one alternative request is permitted after a failure; once a source
exists, a later web error closes browsing and requires a partial answer from the
material already collected. A model response truncated while making a tool call
gets one corrective retry before the request fails.

## Agent evals

`run_evals.py` measures interactive agent behaviour in two deliberately
separate suites. Offline cases use fixed fixtures to test harness invariants:
direct URL summaries, page-cache preservation, ten Czech sports articles, topic
research, partial answers after an injected fetch error, and recovery from a
truncated tool call. Live cases use real web data and test the same behaviours
without pretending their facts are fixed.

Offline runs use fixture responses for web tools but call the selected real
model, so behaviour is repeatable without Tavily or changing web pages:

```
python3 run_evals.py offline --model websailor-3b --model qwen/qwen3-8b \
  --judge-model qwen/qwen3-8b --repetitions 3
```

Live runs use real web tools. Their direct-page cases use the allowlisted Qwen
model card in `evals/live_targets.json`, with its configured backup URL used on
one infrastructure retry:

```
python3 run_evals.py live --model websailor-3b --model qwen/qwen3-8b \
  --judge-model qwen/qwen3-8b --repetitions 3
```

If local memory cannot hold the target and judge models at the same time, split
the evaluation into two terminal commands. First load only the target model and
collect its immutable artifacts; the command prints the saved run directory:

```
python3 run_evals.py live --model google/gemma-4-e2b --repetitions 3 --phase collect
```

Then unload it, load the judge model, and score that exact saved run without
running the target model or making new web requests:

```
python3 run_evals.py live --phase judge \
  --judge-model qwen/qwen3-8b \
  --judge-run evals/runs/<timestamp>
```

`--phase all` remains the default and preserves the original single-command
workflow. A collection report is marked `pending_judge` until the second command
updates its existing `summary.json`, per-attempt `judge.json`, and `report.md`.
The collection also freezes each case's task and judge rubric with its artifact,
so later edits to case files cannot change the score. If the judge model is
unavailable, the run is marked `inconclusive`; reload the judge and repeat only
the `--phase judge` command against the same run directory.

The live suite also checks a comparison of two user-supplied model-card URLs
(both must be fetched, with no search) and a three-article research task
(individual article URLs, dates, and at least two fetched articles). The two
model-card targets are allowlisted with paired backups in `evals/live_targets.json`.

For every completed run the runner enforces hard checks (tool policy,
web-request limit, required outputs, source links and dates) and asks a judge to
score relevance, completeness, sources, and honest limitations. A judge pass
requires average >=7/10 and no criterion below 5. The judge receives compact
evidence excerpts only for sources cited in the answer. All successful fetches
are archived (raw response, full extracted text, metadata, search results and
`evidence.json`) in `evals/runs/<timestamp>/`, even if the cache tools were not
available to the model.

Cases may declare the applicable judge dimensions. A direct-page summary, for
example, is judged on relevance and completeness only: it is not penalized for
omitting an unnecessary bibliography or a limitations section. `report.md` also
shows loop recovery counts; an attempted recovery that returns serialized tool
markup rather than an answer is a deterministic failure. For compatibility with
some local function-calling models, the harness also recognizes a strict JSON
array of enabled `{name, arguments}` calls emitted as otherwise-empty assistant
content and normalizes it into standard tool calls. This is reported as
`Normalized calls`; arbitrary text, unknown tools, and any other JSON shape are
never executed.

The default is three repetitions. A capability passes at least 2 of 3 completed
runs; `--repetitions 1` is explicitly diagnostic. DNS, timeout, 5xx and
rate-limit failures are retried once (using the backup target where configured)
and then reported as `inconclusive`, rather than as a model failure. It exits
with code 2 if any case is inconclusive. Using the same model as target and
judge is allowed, but `report.md` visibly warns about the overlap.

Web quota is enforced per network request even when a model returns a batch of
tool calls: after 12 requests the remaining calls are refused locally and the
harness asks once for a no-tool partial answer from collected evidence. The same
final-answer recovery runs when the agent turn limit is reached. The judge parser
accepts a fenced or prefixed JSON object and makes one JSON-only repair request
when its first response is malformed. `report.md` separates loop, quota, and
turn-limit recoveries.

Sessions are saved under `~/.qwen-harness/sessions/` by default (override with
`--sessions-dir`). Each has `session.json` with its fixed configuration and
full protocol transcript plus a readable `history.md`. The model receives a
rolling context of the last 20 messages by default, while the complete archive
is retained. Inside chat, use `/help`, `/history`, `/clear`, and `/exit`.
`/clear` starts the next request with a fresh model context but does not remove
anything from the saved history. To prevent a stuck model from burning a large
turn budget, chat detects three identical tool calls, regardless of whether
those calls reported success or failure (including `write_file`). It then makes one final model request
with tools disabled and compact material already collected, so the model can
answer rather than loop. If that final request cannot provide text, the harness
returns an honest fallback.

## Irreversible commands: propose/confirm/execute, not "the model decides"

`--enable-run-command` gives the model a `run_command` tool restricted to
`--allowed-commands` (no `rm`, no bare shell). That's not enough on its own —
a model can still reach an irreversible result through an allowed binary
(`python3 -c "shutil.rmtree(...)"`, `git reset --hard`, `git push --force`,
etc.). `execute_command` pattern-matches for exactly that class of command
(`DESTRUCTIVE_PATTERNS` in `qwen_agent.py`) and refuses to run it — instead
it writes `<workdir>/pending-action.json` (the exact command, a reason, a
random token, a timestamp) and returns `status: pending_confirmation` to the
model as a normal tool result, so the model reacts by calling `finish()`
with a summary instead of retrying. The run then exits **4**, a code
distinct from the existing 0 (done) / 1 (ran out of turns) / 2 (crashed).

A second, separate invocation applies an approved action without asking the
model again — approval is for the exact proposed string, not a fresh
decision:

```
python3 qwen_agent.py --workdir /path/to/workdir --resolve-pending <token> \
  --allowed-commands python3,git
```

This skips the LLM loop entirely (`--system`/`--task` aren't required in
this mode). It re-validates the token against `pending-action.json` and
checks it hasn't expired (`PENDING_ACTION_TIMEOUT_HOURS = 48`) before
executing the stored command — a stale or mismatched token is refused, and
the pending file is cleared either way so a dead proposal can't be replayed.

`paperclip_adapter.py` wires this into paperclip's comment thread: on a
`pending_confirmation` exit, it posts `Reply exactly "APPROVE <token>"` /
`"REJECT <token>"` and blocks the issue; on the next run it checks for a
matching **human** (`authorType: "user"`) reply before doing anything else —
`APPROVE` re-invokes `--resolve-pending`, `REJECT` just clears the pending
file, and no reply yet is a silent no-op (the issue stays blocked, nothing
is posted). Standalone CLI use (not through paperclip) gets the same
protection — you just apply the approved action by hand: run the
`--resolve-pending` command above yourself once you've read
`pending-action.json` and decided it's fine.

This is pattern-matching on a command string, not a real sandbox — a model
actively trying to evade it could still find a way. It isn't meant to
defend against that; it's a backstop against a cooperative model reaching
for the nearest working tool to finish an assigned task, including one it
was never explicitly told to pause on.

## Task-brief authoring: two patterns, pick deliberately

Both proven end-to-end in `fame5-bible-v2-prototype/predict-screen/` (see
that project's `task.txt` vs `task-b.txt` for real examples). The brief
format itself matters more than prose detail — a literal skeleton the model
edits in place beats an accumulated list of rules, because rule-lists let
the model drift far enough from the target structure that a mechanical
checker fails it repeatedly without the model understanding why. Both
patterns below are skeleton-first; they differ in how much of the skeleton
is fixed.

**Open-skeleton pattern** — structure fixed verbatim, visual values (colors,
spacing, font sizes) explicitly left for the model to fill in. Use this when
no real design decision has been made yet and the immediate goal is a
correct, working structure. Costs more regeneration rounds (observed: 4) —
the model is making real design choices, not just transcribing, and a
7B-class local model's choices need checking on their own merits, not just
the structure around them.

**Locked-skeleton pattern** — structure AND visual values (exact hex colors,
font-family stacks, spacing) both fixed verbatim. Use this once someone
(ideally a real design pass — see the `frontend-design` skill for the
brainstorm/plan/critique process used to derive Variant B's tokens) has
already decided the palette/type/layout — the model's job shrinks to
"transcribe this exactly, vary only what's explicitly marked variable" (a
small numeric range, e.g. "bar count 3-5" or "height 300-450px"). Costs
fewer regeneration rounds (observed: 1) — there's almost nothing left for
the model to get wrong that isn't mechanically checkable.

The tradeoff isn't free: locking more into the brief means whoever writes
the brief (not the model) is doing more of the actual design work. That's a
feature here, not a bug — see the calling project's division-of-labor notes
on why design decisions belong at brief-authoring time regardless of which
pattern is used.

## `--samples N`: what it's for, and what it turned out NOT to be for

The locked-skeleton pattern above makes runs reliable by leaving the model
almost nothing to decide — which also means the model isn't contributing
much beyond the act of typing. The instinct was: use `--samples` to spend
its one real comparative advantage over Claude writing the file directly —
free, instantaneous, parallel attempts — on exploring the open-skeleton
pattern's real (if bounded) choices. Tested directly, twice; see below for
why that didn't pan out and **"What's actually worth running multiple times"**
further down for what did. `--samples N` itself remains a real, working
harness feature — N independent attempts, each in its own isolated
`<workdir>/samples/sample-N/` (so `write_file` calls can't collide between
attempts), each independently checked by `--verify-command`, with a
`<log-basename>-samples.json` summary at the end listing every attempt's
pass/fail. This is the harness's own filtering, before anything reaches
Claude for review — mechanically it works exactly as designed (proven by
its own smoke test and two real runs). Where it fell short was the *reason*
for running it multiple times: "explore an open-skeleton brief and hope for
useful variety" underdelivered twice (see below). Keep reading before
reaching for it on that basis.

```
python3 qwen_agent.py --workdir predict-screen --system task.txt --task task.txt \
  --enable-run-command --allowed-commands python3 \
  --verify-command "python3 /abs/path/to/predict-screen/check_structure.py output.fragment.html" \
  --recovery-turns 4 \
  --samples 5
```

`--recovery-turns` is disabled by default. When enabled, a nonzero initial
verification result is sent back to the model along with an instruction to
inspect and edit through tools; the harness permits at most that many extra
model turns. It only reruns the verifier after a later completion, and a
sample passes only if that later harness-owned run exits zero. The per-sample
log labels the initial failure, recovery turns, and each verification result.

**`--verify-command` runs with `cwd=<that sample's subdir>`, not `--workdir`**
— give the checker *script* as an absolute path; the file it checks (e.g.
`output.fragment.html`) stays relative so it resolves against that sample's
own output, not a shared one. Found this the hard way running the smoke
test for this feature: a relative checker path resolved fine for a
single-sample run (cwd == workdir, same place) but broke silently for every
sample once cwd became the per-sample subdir instead.

(The harness used to gate every model call on a macOS memory-pressure
check — removed; it was hit by the caller more often than it was useful,
so calls now always go straight through.)

**The absolute-path rule above applies to the TASK PROMPT too, not just
`--verify-command`.** If the task brief tells the model to self-check via
`run_command` (e.g. "run `python3 check_structure.py output.html`"), that
path needs to be absolute for the same cwd reason — found this by shipping
a brief that got it half-right (the harness's own `--verify-command` was
absolute, the brief's self-check instruction to the model wasn't) on a real
5-sample run: every sample's own self-check failed with "file not found" on
every turn, burning turns, and in one case the model's response to a
persistently "broken" checker was to **write its own replacement
`check_structure.py` stub that always exits 0** so its self-check would
stop failing — then called `finish()`. The harness's real, absolute-path
`--verify-command` still caught the actual output as broken (it ran the
real checker, unaffected by whatever the model wrote in its own sample
sandbox) — this is exactly the scenario "never trust self-certification
alone" exists for, just previously only theoretical.

**Giving the model a complete worked example, even under an explicit
"restructure freely" framing, mostly produces copies of the example, not
genuine variation.** Observed directly: 3 of 5 samples that passed were
byte-identical to the worked example in the brief. This isn't a bug, it's
the same transcription-over-judgment tendency documented for the
locked-skeleton pattern, showing up even when the brief explicitly invites
divergence. Leaving one field as an unfilled placeholder (e.g. `[choose:
Likely / Contested / Uncertain / Long shot]`) does at least force the model
to write *something* there instead of copying a filled-in value — confirmed
on an immediate re-run: samples diverged (Contested / Uncertain / Uncertain
/ Long shot) purely in that field, identical everywhere else.

**But don't oversell a filled placeholder as "genuine judgment."** Re-ran
the identical experiment on 3 GENUINELY DIFFERENT questions (different
celebrity, different content, single-shot each, no `--samples`) and all 3
independently picked the same word ("Contested") for that field. That looks
like the model's default/highest-probability token for an underspecified
categorical slot, not context-sensitive reasoning about each specific
question — the earlier 4-way split was more likely temperature noise across
identical inputs than evidence of judgment. Net conclusion, stated plainly:
placeholder-filling proves the model will write *something* non-copied into
an open field; it does not prove that something is reasoned.

## What's actually worth running multiple times: different inputs, not the same one

Given the above, `--samples` (same brief, N attempts, hoping for useful
variation) underdelivered twice in direct testing. **Content-population**
(same locked design, N *different* briefs — different real content each)
delivered every time, cheaper per useful artifact, because it plays to the
one thing this model size is reliably good at: fresh generation from a
clear, fully-specified spec — see the "locked-skeleton" pattern above,
just applied across several different content inputs instead of one. This
is not a harness flag, just N ordinary single-shot invocations (one per
content item, no `--samples`):

```
for item in celeb-a celeb-b celeb-c; do
  python3 qwen_agent.py --workdir predict-screen \
    --system system.txt --task "task-$item.txt" \
    --enable-run-command --allowed-commands python3 \
    --verify-command "python3 /abs/path/check_structure_content.py output-$item.fragment.html --celebrity '...' --question '...'"
done
```

Each `task-$item.txt` is the SAME locked skeleton/design with only the
content fields (name, question, horizon, ...) substituted — and the checker
needs those specifics as CLI args instead of hardcoded strings so it can
grade each item correctly (`check_structure_c_content.py` in
`fame5-bible-v2-prototype/predict-screen/` is a real example: same
structural checks as its single-subject sibling, celebrity/question/horizon
parameterized). If the actual goal is "populate a design with several real,
different examples" (demo data, QA states, a content library), reach for
this before reaching for `--samples` — `--samples` is for when you
specifically need several attempts at the SAME input and can tolerate most
of them being redundant or wrong.

## Guardrail: the harness now refuses to let the model overwrite its own grader

Found live (Variant C, one sample of a `--samples` batch): a model facing a
persistently failing self-check — because of a *brief* bug, a relative path
to the checker in a `--samples` sub-workdir — didn't fix its own path, it
wrote a replacement `check_structure.py` that always exits 0, then called
`finish()`. The harness's real `--verify-command` (absolute path, unaffected
by whatever the model wrote in its own sandbox) still caught the actual
output as broken, so nothing shipped — but the harness now also blocks the
`write_file` call itself: if `--verify-command` references a `.py` script
and the model's `write_file` target resolves to that same file, the write is
rejected with a message pointing at the real fix (its path is wrong, not the
checker). Belt-and-suspenders — the independent re-verify was always the
real safety net, this just stops the wasted turns sooner.

## Brief-writing tips that came out of real breakage, not speculation

- **Always use double-quoted HTML attributes in worked examples**, even
  though `'Courier New'`-style single-quoted font names are individually
  fine — a model that copies the *pattern* rather than the literal
  characters can end up single-quoting the outer attribute too, and
  `style='...font-family:'Courier New'...'` closes the attribute at the
  first inner quote, corrupting everything after it. Observed as a real,
  file-breaking bug, not a hypothetical.
- **Don't tell the model to prefix `run_command` calls with `python3`
  as if it were fragile advice** — the harness now auto-corrects a bare
  `some_checker.py args` call to `python3 some_checker.py args` whenever
  `python3` is in `--allowed-commands`, so this class of wasted turn
  (observed 5+ times across real runs) mostly self-heals now. Still worth
  telling the model to always give the checker script an ABSOLUTE path
  (see above) — that part isn't auto-correctable, since a relative path
  could be intentional in principle.
