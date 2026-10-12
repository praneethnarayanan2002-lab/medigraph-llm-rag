"""Score every run in eval/runs/ against the golden set and write eval/reports/results.md.

Metrics (all computed against gold answers derived from the graph, no LLM judge):
  answer_recall      share of gold entities the answer names
  unsupported_rate   share of entities named in the answer that are NOT in the gold set
                     (hallucinations, or true-but-ungrounded claims) - lower is better
  exec_accuracy      graphrag only: the query's result set equals the gold set exactly
  retrieval_recall   graphrag only: share of gold entities present in the retrieved rows
  valid_first_try    graphrag only: generated Cypher passed every check on the first attempt
  count / yes-no     correct value stated in the answer
  refusal_correct    unanswerable questions answered with "not found / can't answer"
  false_refusal      answerable questions wrongly refused
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.graph.db import get_db  # noqa: E402

GOLDEN = ROOT / "eval" / "golden_set.jsonl"
RUNS = ROOT / "eval" / "runs"
REPORT = ROOT / "eval" / "reports" / "results.md"

REFUSAL = re.compile(
    r"couldn.t find|could not find|not (?:found|present|available|contained|included)|"
    r"no (?:information|data|record)|can.t answer|cannot answer|unable to|not in the knowledge graph|"
    r"doesn.t (?:contain|include|have)|does not (?:contain|include|have)|i don.t know|consult|talk to a",
    re.IGNORECASE,
)


@lru_cache
def names_by_label(label: str) -> frozenset[str]:
    rows = get_db().read(f"MATCH (n:{label}) RETURN n.name AS name", max_rows=200_000).rows
    return frozenset(r["name"] for r in rows if r["name"])


def mentioned(answer: str, label: str) -> set[str]:
    """Names of `label` nodes that appear in the answer text."""
    if not answer:
        return set()
    if label == "Gene":  # symbols: exact, case-sensitive token match
        tokens = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", answer))
        return {n for n in names_by_label(label) if n in tokens and len(n) >= 2}
    text = " " + re.sub(r"\s+", " ", answer.lower()) + " "
    found = set()
    # Longest names first; each match is blanked out so "Estradiol" only counts if it also
    # appears on its own, not as part of an already-matched "Estradiol acetate".
    for n in sorted(names_by_label(label), key=len, reverse=True):
        if len(n) < 4:
            continue
        low = n.lower()
        idx = text.find(low)
        while idx != -1:
            end = idx + len(low)
            if not text[idx - 1].isalnum() and (end >= len(text) or not text[end].isalnum()):
                found.add(n)
                text = text[:idx] + "#" * len(low) + text[end:]
            idx = text.find(low, idx + 1)
    return found


def strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    return []


def predicted_bool(answer: str) -> bool | None:
    head = answer.lower()[:200]
    if re.search(r"\b(no|not)\b", head):
        return False
    if re.search(r"\byes\b|\bis contraindicated\b", head):
        return True
    return None


def score(item: dict, rec: dict) -> dict:
    answer = rec.get("answer") or ""
    out: dict = {"category": item["category"], "latency": rec.get("latency_s")}
    refused = rec.get("status") in {"out_of_scope", "no_answer"} or (
        bool(REFUSAL.search(answer[:300])) and item["kind"] != "refuse"
        and not mentioned(answer, item.get("answer_label", "Drug"))
    )
    if item["kind"] == "refuse":
        out["refusal_correct"] = rec.get("status") in {"out_of_scope", "no_answer"} or bool(REFUSAL.search(answer))
        return out
    out["false_refusal"] = refused

    if rec.get("system") == "graphrag":
        out["valid_first_try"] = rec.get("first_try_valid", False)
        out["attempts"] = rec.get("attempts", 0)
        out["used_fallback"] = rec.get("used_fallback", False)

    if item["kind"] == "list":
        gold = set(item["gold"])
        label = item["answer_label"]
        named = mentioned(answer, label)
        out["answer_recall"] = len(gold & named) / len(gold)
        out["unsupported_rate"] = len(named - gold) / len(named) if named else 0.0
        out["named"] = len(named)
        if rec.get("system") == "graphrag":
            retrieved = {s for s in strings(rec.get("rows", [])) if s in names_by_label(label)}
            out["retrieval_recall"] = len(gold & retrieved) / len(gold)
            out["exec_accuracy"] = (retrieved == gold) and not rec.get("used_fallback")
    elif item["kind"] == "count":
        out["value_correct"] = str(item["gold"]) in re.findall(r"\d+", answer)
    elif item["kind"] == "boolean":
        out["value_correct"] = predicted_bool(answer) == item["gold"]
    return out


def mean(values) -> float | None:
    values = [float(v) for v in values if v is not None]
    return statistics.mean(values) if values else None


def fmt(v, pct=True) -> str:
    if v is None:
        return "–"
    return f"{v * 100:.1f}%" if pct else f"{v:.1f}"


def summarize(scores: list[dict]) -> dict:
    def pick(key, cats=None):
        return [s[key] for s in scores if key in s and (cats is None or s["category"] in cats)]

    lat = pick("latency")
    return {
        "n": len(scores),
        "answer_recall": mean(pick("answer_recall")),
        "unsupported_rate": mean(pick("unsupported_rate")),
        "exec_accuracy": mean(pick("exec_accuracy")),
        "retrieval_recall": mean(pick("retrieval_recall")),
        "valid_first_try": mean(pick("valid_first_try")),
        "value_correct": mean(pick("value_correct")),
        "refusal_correct": mean(pick("refusal_correct")),
        "false_refusal": mean(pick("false_refusal")),
        "fallback_rate": mean(pick("used_fallback")),
        "latency_p50": statistics.median(lat) if lat else None,
        "latency_p95": sorted(lat)[int(0.95 * (len(lat) - 1))] if lat else None,
    }


def main() -> None:
    golden = {json.loads(line)["id"]: json.loads(line) for line in GOLDEN.open()}
    runs = sorted(RUNS.glob("*.jsonl"))
    if not runs:
        sys.exit("No runs found in eval/runs/. Run eval/run_eval.py first.")

    summaries, per_category = {}, {}
    for path in runs:
        records = [json.loads(line) for line in path.open()]
        scores = [score(golden[r["id"]], r) for r in records if r["id"] in golden]
        summaries[path.stem] = summarize(scores)
        by_cat = defaultdict(list)
        for s in scores:
            by_cat[s["category"]].append(s)
        per_category[path.stem] = {c: summarize(v) for c, v in by_cat.items()}

    lines = ["# MediGraph evaluation results", "",
             f"Golden set: {len(golden)} questions (`eval/golden_set.jsonl`). "
             "Gold answers come from reference Cypher queries over PrimeKG.", "",
             "## Overall", "",
             "| run | n | answer recall | unsupported claims ↓ | exec accuracy | retrieval recall | "
             "valid 1st try | count/yes-no | refusal correct | false refusal ↓ | fallback | p50 s | p95 s |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in summaries.items():
        lines.append(
            f"| {name} | {s['n']} | {fmt(s['answer_recall'])} | {fmt(s['unsupported_rate'])} | "
            f"{fmt(s['exec_accuracy'])} | {fmt(s['retrieval_recall'])} | {fmt(s['valid_first_try'])} | "
            f"{fmt(s['value_correct'])} | {fmt(s['refusal_correct'])} | {fmt(s['false_refusal'])} | "
            f"{fmt(s['fallback_rate'])} | {fmt(s['latency_p50'], False)} | {fmt(s['latency_p95'], False)} |"
        )
    lines += ["", "## Answer recall by category", ""]
    cats = sorted({c for pc in per_category.values() for c in pc})
    lines.append("| category | " + " | ".join(per_category) + " |")
    lines.append("|---|" + "---|" * len(per_category))
    for c in cats:
        cells = []
        for run_name in per_category:
            s = per_category[run_name].get(c)
            if not s:
                cells.append("–")
            elif s["answer_recall"] is not None:
                cells.append(f"{fmt(s['answer_recall'])} (unsupp. {fmt(s['unsupported_rate'])})")
            elif s["value_correct"] is not None:
                cells.append(f"{fmt(s['value_correct'])} correct")
            else:
                cells.append(f"{fmt(s['refusal_correct'])} refused")
        lines.append(f"| {c} | " + " | ".join(cells) + " |")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    (REPORT.parent / "results.json").write_text(json.dumps({"overall": summaries, "by_category": per_category}, indent=2))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
