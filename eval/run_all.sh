#!/usr/bin/env bash
# Queue of evaluation runs, executed one at a time (one LLM in memory on a 16 GB Mac).
set -uo pipefail
cd "$(dirname "$0")/.."
run() { echo "=== $(date '+%H:%M') start: $*"; uv run python eval/run_eval.py "$@" 2>&1 | grep -v -i "loading weights"; echo "=== $(date '+%H:%M') done: $*"; }

run --system graphrag --name graphrag__qwen3.5-9b__v2-fixes
run --system closed_book
ollama stop qwen3.5:9b >/dev/null 2>&1
run --system graphrag --model llama3.1:latest --per-category 5
ollama stop llama3.1:latest >/dev/null 2>&1
uv run python eval/metrics.py > /dev/null && echo "=== metrics written to eval/reports/results.md"
