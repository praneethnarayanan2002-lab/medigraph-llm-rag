"""Build the evaluation set: questions with gold answers computed from the graph.

Most questions come from templates filled with entities sampled from PrimeKG; the gold answer
is the result of a hand-written reference Cypher query, so it is exact rather than judged.
Entities that appear in the few-shot examples are excluded to avoid leakage.
Hand-written groups cover lay terms, follow-ups and questions the system must refuse.

Usage: uv run python eval/build_golden.py   ->  eval/golden_set.jsonl
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.graph.db import get_db  # noqa: E402
from app.pipeline.examples import EXAMPLES  # noqa: E402

OUT = ROOT / "eval" / "golden_set.jsonl"
PER_TEMPLATE = 12
SEED = 7

# Every entity name quoted in a few-shot example is off-limits for the eval.
LEAKED = {n.lower() for _, c in EXAMPLES for n in re.findall(r"'([^']+)'", c)} | {
    "asthma", "hypertension", "type 2 diabetes mellitus", "metformin", "cystic fibrosis",
}

# category: (question templates, reference cypher with $name, label of answer entities, (min, max) answers)
LIST_TEMPLATES = {
    "drugs_for_disease": (
        ["What drugs are used to treat {name}?", "Which medications are indicated for {name}?",
         "What is {name} treated with?"],
        "MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: $name}) RETURN DISTINCT d.name AS answer",
        "Disease", "Drug", (3, 30),
    ),
    "drug_side_effects": (
        ["What side effects can {name} cause?", "What are the adverse effects of {name}?",
         "List the side effects of {name}."],
        "MATCH (:Drug {name: $name})-[:HAS_SIDE_EFFECT]->(p:Phenotype) RETURN DISTINCT p.name AS answer",
        "Drug", "Phenotype", (3, 35),
    ),
    "disease_genes": (
        ["Which genes are associated with {name}?", "What genes are linked to {name}?"],
        "MATCH (:Disease {name: $name})-[:ASSOCIATED_WITH_GENE]->(g:Gene) RETURN DISTINCT g.name AS answer",
        "Disease", "Gene", (3, 30),
    ),
    "drug_targets": (
        ["What proteins does {name} target?", "What are the molecular targets of {name}?"],
        "MATCH (:Drug {name: $name})-[:TARGETS]->(g:Gene) RETURN DISTINCT g.name AS answer",
        "Drug", "Gene", (2, 20),
    ),
    "contraindications": (
        ["Which drugs are contraindicated in {name}?", "What drugs should be avoided in patients with {name}?"],
        "MATCH (d:Drug)-[:CONTRAINDICATED_FOR]->(:Disease {name: $name}) RETURN DISTINCT d.name AS answer",
        "Disease", "Drug", (3, 30),
    ),
    "disease_phenotypes": (
        ["What are the clinical features of {name}?", "What symptoms and signs are seen in {name}?"],
        "MATCH (:Disease {name: $name})-[:HAS_PHENOTYPE]->(p:Phenotype) RETURN DISTINCT p.name AS answer",
        "Disease", "Phenotype", (3, 30),
    ),
    "multihop_drugs_via_genes": (
        ["Which drugs target genes that are associated with {name}?",
         "What drugs act on proteins linked to {name}?"],
        "MATCH (:Disease {name: $name})-[:ASSOCIATED_WITH_GENE]->(:Gene)<-[:TARGETS]-(d:Drug) "
        "RETURN DISTINCT d.name AS answer",
        "Disease", "Drug", (2, 25),
    ),
    "multihop_shared_targets": (
        ["Which other drugs share a protein target with {name}?"],
        "MATCH (:Drug {name: $name})-[:TARGETS]->(:Gene)<-[:TARGETS]-(d:Drug) WHERE d.name <> $name "
        "RETURN DISTINCT d.name AS answer",
        "Drug", "Drug", (2, 25),
    ),
}


def clean_name(name: str) -> bool:
    return 4 <= len(name) <= 45 and not re.search(r"[(),/;]", name) and name.lower() not in LEAKED


def sample_subjects(db, label: str, cypher: str, bounds: tuple[int, int], rng: random.Random) -> list[tuple]:
    rows = db.read(
        f"""
        MATCH (s:{label})
        WITH s.name AS name
        CALL (name) {{ {cypher.replace("$name", "name")} }}
        WITH name, collect(answer) AS answers
        WHERE size(answers) >= $lo AND size(answers) <= $hi
        RETURN name, answers
        """,
        {"lo": bounds[0], "hi": bounds[1]},
        max_rows=100_000,
        timeout=300,
    ).rows
    rows = [r for r in rows if clean_name(r["name"])]
    rng.shuffle(rows)
    return [(r["name"], sorted(r["answers"])) for r in rows]


def main() -> None:
    db = get_db()
    rng = random.Random(SEED)
    items: list[dict] = []

    for category, (templates, cypher, subj_label, ans_label, bounds) in LIST_TEMPLATES.items():
        subjects = sample_subjects(db, subj_label, cypher, bounds, rng)[:PER_TEMPLATE]
        for i, (name, answers) in enumerate(subjects):
            items.append({
                "category": category, "kind": "list",
                "question": templates[i % len(templates)].format(name=name),
                "subject": name, "answer_label": ans_label, "gold": answers,
                "reference_cypher": cypher.replace("$name", json.dumps(name)),
            })
        print(f"{category:28s} {len(subjects)}")

    # Counts
    count_cypher = "MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: $name}) RETURN DISTINCT d.name AS answer"
    for name, answers in sample_subjects(db, "Disease", count_cypher, (2, 40), rng)[:8]:
        items.append({
            "category": "count", "kind": "count",
            "question": f"How many drugs are indicated for {name}?",
            "subject": name, "gold": len(answers),
            "reference_cypher": count_cypher.replace("$name", json.dumps(name)).replace(
                "RETURN DISTINCT d.name AS answer", "RETURN count(DISTINCT d) AS answer"),
        })

    # Yes / no, balanced
    pos = db.read(
        "MATCH (d:Drug)-[:CONTRAINDICATED_FOR]->(x:Disease) RETURN d.name AS drug, x.name AS disease",
        max_rows=50_000,
    ).rows
    pos = [r for r in pos if clean_name(r["drug"]) and clean_name(r["disease"])]
    rng.shuffle(pos)
    for i, r in enumerate(pos[:10]):
        drug, disease = r["drug"], r["disease"]
        if i % 2:  # make a negative by pairing the drug with a disease it is NOT contraindicated for
            disease = next(
                p["disease"] for p in pos
                if not db.read(
                    "MATCH (:Drug {name: $d})-[r:CONTRAINDICATED_FOR]->(:Disease {name: $x}) RETURN r",
                    {"d": drug, "x": p["disease"]},
                ).rows
            )
        items.append({
            "category": "yes_no", "kind": "boolean",
            "question": f"Is {drug} contraindicated in {disease}?",
            "subject": drug, "gold": i % 2 == 0,
            "reference_cypher": f"MATCH (d:Drug {{name: {json.dumps(drug)}}}), (x:Disease {{name: "
            f"{json.dumps(disease)}}}) RETURN EXISTS {{ (d)-[:CONTRAINDICATED_FOR]->(x) }} AS answer",
        })

    # Lay terms: tests entity linking, gold computed via the canonical name.
    lay = [
        ("What medicines treat a stroke?", "drugs_for_disease", "stroke disorder"),
        ("What drugs are used for heart failure?", "drugs_for_disease", "heart failure"),
        ("What drugs are used to treat gout?", "drugs_for_disease", "gout"),
        ("Which drugs treat high cholesterol?", "drugs_for_disease", "hypercholesterolemia"),
        ("Which drugs treat an underactive thyroid?", "drugs_for_disease", "hypothyroidism"),
        ("What medications treat acid reflux?", "drugs_for_disease", "gastroesophageal reflux disease"),
        ("What side effects can Tylenol cause?", "drug_side_effects", "Acetaminophen"),
        ("What are the side effects of Zoloft?", "drug_side_effects", "Sertraline"),
        ("What are the side effects of Lasix?", "drug_side_effects", "Furosemide"),
        ("What genes are linked to sickle cell anemia?", "disease_genes", "sickle cell anemia"),
        ("What genes are linked to Lou Gehrig's disease?", "disease_genes", "amyotrophic lateral sclerosis"),
        ("What drugs are used for a heart attack?", "drugs_for_disease", "myocardial infarction"),
    ]
    for question, category, canonical in lay:
        _, cypher, _, ans_label, _ = LIST_TEMPLATES[category]
        gold = sorted(r["answer"] for r in db.read(cypher, {"name": canonical}, max_rows=500).rows)
        if not gold:
            print(f"  skip lay question (no gold): {canonical}")
            continue
        items.append({
            "category": "lay_terms", "kind": "list", "question": question, "subject": canonical,
            "answer_label": ans_label, "gold": gold,
            "reference_cypher": cypher.replace("$name", json.dumps(canonical)),
        })

    # Follow-ups: the first turn's gold answer is given as history, the second turn must resolve "those".
    fu_cypher = (
        "MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: $disease}) "
        "MATCH (d)-[:HAS_SIDE_EFFECT]->(:Phenotype {name: $effect}) RETURN DISTINCT d.name AS answer"
    )
    pairs = db.read(
        """
        MATCH (d:Drug)-[:INDICATED_FOR]->(x:Disease)
        WITH x, collect(d) AS drugs WHERE size(drugs) >= 4 AND size(drugs) <= 15
        UNWIND drugs AS d MATCH (d)-[:HAS_SIDE_EFFECT]->(p:Phenotype)
        WITH x, drugs, p, count(d) AS n WHERE n >= 2 AND n < size(drugs)
        RETURN x.name AS disease, [d IN drugs | d.name] AS drugs, p.name AS effect
        """,
        max_rows=5000,
        timeout=120,
    ).rows
    pairs = [p for p in pairs if clean_name(p["disease"]) and clean_name(p["effect"])]
    rng.shuffle(pairs)
    seen = set()
    for p in pairs:
        if p["disease"] in seen or len(seen) >= 10:
            continue
        seen.add(p["disease"])
        gold = sorted(r["answer"] for r in db.read(fu_cypher, {"disease": p["disease"], "effect": p["effect"]}).rows)
        items.append({
            "category": "follow_up", "kind": "list",
            "history": [
                {"role": "user", "content": f"What drugs are used to treat {p['disease']}?"},
                {"role": "assistant", "content": f"Drugs indicated for {p['disease']}: " + ", ".join(sorted(p["drugs"])) + "."},
            ],
            "question": f"Which of those can cause {p['effect'].lower()}?",
            "subject": p["disease"], "answer_label": "Drug", "gold": gold,
            "reference_cypher": fu_cypher.replace("$disease", json.dumps(p["disease"])).replace(
                "$effect", json.dumps(p["effect"])),
        })

    # Must refuse / report not found.
    for q in [
        "What dose of ibuprofen should I take for a headache?",
        "Should I stop taking my blood pressure medication?",
        "How much does insulin glargine cost?",
        "What is the prevalence of diabetes in India?",
        "What were the results of the latest clinical trial for semaglutide?",
        "Can you diagnose my chest pain?",
        "What drugs treat Zorvathic syndrome?",
        "What are the side effects of Floxabutrin?",
        "Which genes are associated with Kessler-Moravian disease?",
        "Who discovered penicillin?",
        "What is the best hospital for cancer treatment?",
        "How many milligrams of aspirin are in a baby aspirin?",
    ]:
        items.append({"category": "unanswerable", "kind": "refuse", "question": q, "gold": None})

    with OUT.open("w") as f:
        for i, item in enumerate(items):
            f.write(json.dumps({"id": f"q{i:03d}", **item}) + "\n")
    print(f"\nwrote {len(items)} questions to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
