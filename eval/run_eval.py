"""Run a system over the golden set and record per-question results (resumable).

Systems:
  graphrag     the full pipeline (default model from .env, or --model)
  closed_book  the same LLM answering from its own memory, no retrieval (the baseline)

Usage:
  uv run python eval/run_eval.py --system graphrag
  uv run python eval/run_eval.py --system graphrag --model llama3.1:latest
  uv run python eval/run_eval.py --system closed_book
  uv run python eval/run_eval.py --system graphrag --provider openai     # needs OPENAI_* in .env
Then: uv run python eval/metrics.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.llm import get_chat_model  # noqa: E402
from app.pipeline.workflow import run  # noqa: E402

GOLDEN = ROOT / "eval" / "golden_set.jsonl"
RUNS = ROOT / "eval" / "runs"

CLOSED_BOOK_PROMPT = """You are a medical knowledge assistant. Answer the question concisely from your
own knowledge. When the answer is a set of items (drugs, genes, symptoms...), list their specific
names. If you don't know, say so.

{history}Question: {question}"""


def run_graphrag(item: dict, provider: str | None, model: str | None) -> dict:
    state = run(item["question"], item.get("history"), provider=provider, model=model)
    log = state.get("attempt_log", [])
    return {
        "answer": state.get("answer", ""),
        "status": state.get("status"),
        "standalone_question": state.get("standalone_question"),
        "entities": [{k: e[k] for k in ("mention", "name", "label", "score", "method")} for e in state.get("entities", [])],
        "cypher": state.get("cypher"),
        "attempts": state.get("attempts", 0),
        "first_try_valid": bool(log) and not log[0]["errors"],
        "attempt_errors": [a["errors"] for a in log],
        "rows": state.get("rows", []),
        "truncated": state.get("truncated", False),
        "used_fallback": state.get("used_fallback", False),
        "trace_ms": {t["step"]: t["ms"] for t in state.get("trace", [])},
    }


def run_closed_book(item: dict, provider: str | None, model: str | None) -> dict:
    history = "".join(f"{t['role']}: {t['content']}\n" for t in item.get("history") or [])
    if history:
        history = "Conversation so far:\n" + history + "\n"
    reply = get_chat_model(provider, model).invoke(
        CLOSED_BOOK_PROMPT.format(history=history, question=item["question"])
    )
    return {"answer": str(reply.content).strip(), "status": "closed_book"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", choices=["graphrag", "closed_book"], default="graphrag")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--category", action="append", help="only these categories (repeatable)")
    parser.add_argument("--name", default=None, help="run name (defaults to system + model)")
    args = parser.parse_args()

    s = get_settings()
    provider = args.provider or s.llm_provider
    model = args.model or (s.openai_model if provider == "openai" else s.ollama_model)
    name = args.name or f"{args.system}__{model.replace(':', '-').replace('/', '-')}"
    out = RUNS / f"{name}.jsonl"
    RUNS.mkdir(parents=True, exist_ok=True)

    items = [json.loads(line) for line in GOLDEN.open()]
    if args.category:
        items = [i for i in items if i["category"] in args.category]
    if args.limit:
        items = items[: args.limit]
    done = {json.loads(line)["id"] for line in out.open()} if out.exists() else set()
    todo = [i for i in items if i["id"] not in done]
    print(f"{name}: {len(todo)} to run ({len(done)} already done) -> {out.relative_to(ROOT)}")

    runner = run_graphrag if args.system == "graphrag" else run_closed_book
    with out.open("a") as f:
        for n, item in enumerate(todo, 1):
            start = time.perf_counter()
            try:
                result = runner(item, provider, model)
                error = None
            except Exception as exc:  # noqa: BLE001 - record and continue
                result, error = {"answer": "", "status": "error"}, f"{exc}\n{traceback.format_exc(limit=3)}"
            latency = time.perf_counter() - start
            record = {"id": item["id"], "system": args.system, "model": model, "latency_s": round(latency, 2),
                      "error": error, **result}
            f.write(json.dumps(record, default=str) + "\n")
            f.flush()
            print(f"[{n}/{len(todo)}] {item['id']} {item['category']:24s} {result.get('status')!s:12s} "
                  f"{latency:5.1f}s  {item['question'][:60]}", flush=True)


if __name__ == "__main__":
    main()
