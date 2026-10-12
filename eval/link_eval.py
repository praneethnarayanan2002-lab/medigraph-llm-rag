"""Entity-linking benchmark: general-purpose embeddings (nomic-embed-text) vs SapBERT.

Each case is a lay / brand / abbreviated term and the PrimeKG node it should link to. We score
pure vector retrieval (top-1 / top-5 over the label's vector index) for each embedder, which
isolates the embedding model from the LLM's own term normalisation.

Usage: uv run python eval/link_eval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.graph.db import get_db  # noqa: E402
from app.graph.linker import vector_index_name  # noqa: E402
from app.llm import get_entity_embeddings  # noqa: E402

CASES = [
    ("heart attack", "Disease", "myocardial infarction"),
    ("high blood pressure", "Disease", "hypertension"),
    ("stroke", "Disease", "stroke disorder"),
    ("Lou Gehrig's disease", "Disease", "amyotrophic lateral sclerosis"),
    ("underactive thyroid", "Disease", "hypothyroidism"),
    ("overactive thyroid", "Disease", "hyperthyroidism"),
    ("acid reflux", "Disease", "gastroesophageal reflux disease"),
    ("high blood sugar", "Phenotype", "Hyperglycemia"),
    ("low blood sugar", "Phenotype", "Hypoglycemia"),
    ("hair loss", "Phenotype", "Alopecia"),
    ("fainting", "Phenotype", "Syncope"),
    ("itching", "Phenotype", "Pruritus"),
    ("nosebleed", "Phenotype", "Epistaxis"),
    ("shortness of breath", "Phenotype", "Dyspnea"),
    ("fast heartbeat", "Phenotype", "Tachycardia"),
    ("throwing up", "Phenotype", "Vomiting"),
    ("ringing in the ears", "Phenotype", "Tinnitus"),
    ("double vision", "Phenotype", "Diplopia"),
    ("Tylenol", "Drug", "Acetaminophen"),
    ("paracetamol", "Drug", "Acetaminophen"),
    ("aspirin", "Drug", "Acetylsalicylic acid"),
    ("Zoloft", "Drug", "Sertraline"),
    ("Lasix", "Drug", "Furosemide"),
    ("Lipitor", "Drug", "Atorvastatin"),
    ("Glucophage", "Drug", "Metformin"),
    ("Coumadin", "Drug", "Warfarin"),
    ("Advil", "Drug", "Ibuprofen"),
    ("vitamin C", "Drug", "Ascorbic acid"),
    ("vitamin B12", "Drug", "Cyanocobalamin"),
    ("tobacco smoke", "Exposure", "Tobacco Smoke Pollution"),
]


def evaluate(kind: str) -> tuple[float, float, list[str]]:
    db = get_db()
    emb = get_entity_embeddings(kind)
    top1 = top5 = 0
    misses = []
    for mention, label, gold in CASES:
        rows = db.read(
            "CALL db.index.vector.queryNodes($index, 5, $v) YIELD node RETURN node.name AS name",
            {"index": vector_index_name(label, kind), "v": emb.embed_query(mention)},
        ).rows
        names = [r["name"] for r in rows]
        top1 += bool(names) and names[0] == gold
        top5 += gold in names
        if not names or names[0] != gold:
            misses.append(f"{mention} -> {names[0] if names else '-'} (want {gold})")
    return top1 / len(CASES), top5 / len(CASES), misses


def main() -> None:
    db = get_db()
    missing = [g for _, lab, g in CASES if not db.read(f"MATCH (n:{lab} {{name: $n}}) RETURN 1", {"n": g}).rows]
    if missing:
        print("not in graph (fix the case list):", missing)
    lines = ["| embedder | top-1 | top-5 |", "|---|---|---|"]
    for kind in ("nomic", "sapbert"):
        t1, t5, misses = evaluate(kind)
        lines.append(f"| {kind} | {t1 * 100:.0f}% | {t5 * 100:.0f}% |")
        print(f"\n{kind}: top-1 {t1:.0%}  top-5 {t5:.0%}\n  misses: " + "\n          ".join(misses))
    report = ROOT / "eval" / "reports" / "entity_linking.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        f"# Entity linking: lay terms → PrimeKG nodes ({len(CASES)} cases, vector search only)\n\n"
        + "\n".join(lines) + "\n"
    )
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
