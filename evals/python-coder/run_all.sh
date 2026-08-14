#!/bin/bash
# Run the full python-coder eval suite (all cases x N samples) for one model,
# into a fresh timestamped output dir per case, then print a pass/fail summary.
#
# Usage:
#   evals/python-coder/run_all.sh <model-id> [--samples N] [--recovery-turns N] [--web]
#
# Examples:
#   evals/python-coder/run_all.sh qwen2.5-coder-7b-instruct-mlx
#   evals/python-coder/run_all.sh qwen2.5-coder-3b-instruct-mlx --samples 5
#   evals/python-coder/run_all.sh qwen/qwen3-8b --recovery-turns 4 --web
set -euo pipefail
cd "$(dirname "$0")/../.."

MODEL="${1:?Usage: $0 <model-id> [--samples N] [--recovery-turns N] [--web]}"
shift
SAMPLES=3
RECOVERY_TURNS=0
WEB_FLAG=()
while [ $# -gt 0 ]; do
  case "$1" in
    --samples) SAMPLES="$2"; shift 2 ;;
    --recovery-turns) RECOVERY_TURNS="$2"; shift 2 ;;
    --web) WEB_FLAG=(--enable-web-access); shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

echo "=== loading $MODEL at 32768 context ==="
~/.lmstudio/bin/lms load "$MODEL" -c 32768 -y
~/.lmstudio/bin/lms ps

TS=$(date -u +%Y%m%dT%H%M%SZ)
SAFE_MODEL=$(echo "$MODEL" | tr -c 'A-Za-z0-9_.-' '_')
PY=.venv-python-coder/bin/python

echo "=== full eval run start: $TS, model=$MODEL, samples=$SAMPLES, recovery_turns=$RECOVERY_TURNS ==="
for case in $($PY evals/python-coder/run_case.py --list); do
  OUT="evals/python-coder/runs/${case}--${SAFE_MODEL}-full-${TS}"
  echo "=== [$case] -> $OUT ==="
  "$PY" evals/python-coder/run_case.py "$case" \
    --model "$MODEL" --samples "$SAMPLES" --recovery-turns "$RECOVERY_TURNS" \
    --output-dir "$OUT" "${WEB_FLAG[@]}" || true
  echo "=== [$case] done ==="
done
echo "=== full eval run done: $TS ==="

echo
echo "=== summary: $MODEL @ $TS ==="
"$PY" -c "
import json
from pathlib import Path

ts, safe_model = '$TS', '$SAFE_MODEL'
runs_dir = Path('evals/python-coder/runs')
total_passed = total = 0
for d in sorted(runs_dir.glob(f'*--{safe_model}-full-{ts}')):
    f = d / 'harness-log-samples.json'
    if not f.is_file():
        print(f'{d.name}: MISSING (crashed before writing a summary?)')
        continue
    data = json.load(open(f))
    case = d.name.split(f'--{safe_model}-full-{ts}')[0]
    print(f'{case}: {data[\"passed_count\"]}/{data[\"total\"]}')
    total_passed += data['passed_count']
    total += data['total']
print(f'\nTOTAL: {total_passed}/{total}' + (f' ({100*total_passed/total:.0f}%)' if total else ''))
"
