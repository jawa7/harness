# Python Coder evaluation fixtures

This suite measures coding-agent completion using the harness's existing
`--verify-command` and `--samples` facilities. It has no LLM judge and no
second scorer: each sample passes only when the harness reruns that case's
`pytest` command successfully.

The eight cases are five isolated exercises and three starting-state variants
of `pyfixture-tasktracker`, a synthetic JSON-backed task tracker. The latter
are deliberately synthetic to avoid training-data contamination.

## Prerequisite

Install the test dependencies in the same Python environment that will run
the local model harness. An isolated virtual environment keeps this setup
reversible:

```sh
python3 -m venv .venv-python-coder
.venv-python-coder/bin/python -m pip install -r evals/python-coder/requirements.txt
```

## Run one case

The helper seeds every native harness sample directory before invoking
`qwen_agent.py`; this is necessary because a repo-context task must begin
with its repository already present. The harness still owns isolation,
execution, verification, and its `<task>-samples.json` pass-rate artifact.
It also opts into the harness's bounded, relative-path workspace bootstrap,
so root-level tests such as `test_greet.py` and `test_pagination.py` are visible
to the model before its first action. The optional verifier advisory is clearly
marked as read-only harness configuration.

```sh
.venv-python-coder/bin/python evals/python-coder/run_case.py follow-existing-pattern \
  --model qwen2.5-coder-7b-instruct-mlx --samples 3 --recovery-turns 4
```

`--recovery-turns` is optional and defaults to `0`. It gives each isolated
sample a bounded repair loop after a failed harness-owned verifier run; it
does not add an LLM judge or allow a failed verifier to pass.

Run every case for one model. This loop is sequential: it starts the next
case only after the previous invocation has exited.

```sh
for case in $(.venv-python-coder/bin/python evals/python-coder/run_case.py --list); do
  .venv-python-coder/bin/python evals/python-coder/run_case.py "$case" \
    --model qwen2.5-coder-7b-instruct-mlx --samples 3
done
```

Compare the pinned 7B model with the 3B baseline by completing that same loop
for one model, unloading/reloading it, then starting the next model.
Do not launch model loops concurrently. Each output directory contains the
harness-native `harness-log-samples.json` artifact. The comparison is 16
case/model runs (8 cases × 2 models), with 3 samples in each run. Once all
results are present, print the overall, isolated, repo-context, and per-case
diagnostic slices, and save a reproducibility manifest with:

```sh
.venv-python-coder/bin/python evals/python-coder/summarize.py \
  --model qwen2.5-coder-7b-instruct-mlx \
  --model qwen2.5-coder-3b-instruct-mlx \
  --harness-flag=--samples=3 \
  --harness-flag=--max-turns=16 \
  --harness-flag=--bootstrap-workspace-context \
  --harness-flag=--bootstrap-include-verify-command \
  --harness-flag=--recovery-turns=0 \
  --output evals/python-coder/runs/comparison-report.json \
  --manifest evals/python-coder/runs/comparison-manifest.json
```

The manifest records the compared model IDs, observed sample count for every
case, exact supplied harness flags, fixture revision when the checkout can
identify one, and the output root. All report paths are relative to that root.
The summarizer only aggregates native harness results; it never calls a model,
runs a verifier, or creates a second scorer. It refuses missing or malformed
artifacts so an incomplete model run cannot look like a valid low pass rate.

For each failed sample the report assigns one stable diagnostic category:
`prose-only` (no tool call), `no-edits` (tools but no `write_file`),
`repeated-command` (the same `run_command` arguments appeared more than once),
`verifier-failure`, `timeout`, or `tool-error`. More specific transcript
evidence takes precedence over a later verifier failure, so a prose-only answer
is not hidden behind the verifier's nonzero result. These labels describe the
already-recorded harness outcome only; they never change its score.

The helper refuses to reuse a non-empty output directory so a prior result is
never overwritten. Use `--output-dir` to choose a new immutable run location.
