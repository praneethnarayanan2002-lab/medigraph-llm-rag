"""Few-shot bank for Text2Cypher. The most similar examples to each question are put in the
prompt (dynamic few-shot). These are hand-written and kept separate from the evaluation set:
eval questions use different entities, so the eval does not reward memorised examples.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from app.llm import get_embeddings

EXAMPLES: list[tuple[str, str]] = [
    (
        "What drugs are used to treat asthma?",
        "MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: 'asthma'})\nRETURN DISTINCT d.name AS drug LIMIT 50",
    ),
    (
        "What is psoriasis?",
        "MATCH (d:Disease {name: 'psoriasis'})\nRETURN d.name AS disease, d.description AS description, d.symptoms AS symptoms",
    ),
    (
        "How does atorvastatin work?",
        "MATCH (d:Drug {name: 'Atorvastatin'})\nOPTIONAL MATCH (d)-[:TARGETS]->(g:Gene)\n"
        "RETURN d.name AS drug, d.mechanism AS mechanism, collect(DISTINCT g.name) AS targets",
    ),
    (
        "What are the side effects of ibuprofen?",
        "MATCH (:Drug {name: 'Ibuprofen'})-[:HAS_SIDE_EFFECT]->(p:Phenotype)\nRETURN DISTINCT p.name AS side_effect LIMIT 50",
    ),
    (
        "Which drugs should not be given to people with glaucoma?",
        "MATCH (d:Drug)-[:CONTRAINDICATED_FOR]->(:Disease {name: 'glaucoma'})\nRETURN DISTINCT d.name AS drug LIMIT 50",
    ),
    (
        "What genes are associated with cystic fibrosis?",
        "MATCH (:Disease {name: 'cystic fibrosis'})-[:ASSOCIATED_WITH_GENE]->(g:Gene)\nRETURN DISTINCT g.name AS gene LIMIT 50",
    ),
    (
        "Which proteins does imatinib target?",
        "MATCH (:Drug {name: 'Imatinib'})-[:TARGETS]->(g:Gene)\nRETURN DISTINCT g.name AS gene",
    ),
    (
        "What are the symptoms of Parkinson disease?",
        "MATCH (d:Disease {name: 'Parkinson disease'})\nOPTIONAL MATCH (d)-[:HAS_PHENOTYPE]->(p:Phenotype)\n"
        "RETURN d.symptoms AS symptoms_text, collect(DISTINCT p.name)[..40] AS phenotypes",
    ),
    (
        "Which drugs target genes associated with Alzheimer disease?",
        "MATCH (:Disease {name: 'Alzheimer disease'})-[:ASSOCIATED_WITH_GENE]->(g:Gene)<-[:TARGETS]-(d:Drug)\n"
        "RETURN g.name AS gene, collect(DISTINCT d.name)[..10] AS drugs ORDER BY size(drugs) DESC LIMIT 25",
    ),
    (
        "How many drugs are indicated for hypertension?",
        "MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: 'hypertensive disorder'})\nRETURN count(DISTINCT d) AS drug_count",
    ),
    (
        "Is warfarin contraindicated in peptic ulcer disease?",
        "MATCH (d:Drug {name: 'Warfarin'}), (x:Disease {name: 'peptic ulcer disease'})\n"
        "RETURN d.name AS drug, x.name AS disease, EXISTS { (d)-[:CONTRAINDICATED_FOR]->(x) } AS contraindicated",
    ),
    (
        "Which enzymes metabolise clopidogrel?",
        "MATCH (:Drug {name: 'Clopidogrel'})-[:METABOLIZED_BY]->(g:Gene)\nRETURN DISTINCT g.name AS enzyme",
    ),
    (
        "What drugs interact with fluoxetine?",
        "MATCH (:Drug {name: 'Fluoxetine'})-[:SYNERGISTIC_WITH]-(d:Drug)\nRETURN DISTINCT d.name AS drug LIMIT 50",
    ),
    (
        "Which diseases share the most symptoms with migraine?",
        "MATCH (:Disease {name: 'migraine disorder'})-[:HAS_PHENOTYPE]->(p:Phenotype)<-[:HAS_PHENOTYPE]-(other:Disease)\n"
        "RETURN other.name AS disease, count(DISTINCT p) AS shared_phenotypes ORDER BY shared_phenotypes DESC LIMIT 10",
    ),
    (
        "Which drugs treat both type 2 diabetes and obesity?",
        "MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: 'type 2 diabetes mellitus'})\n"
        "MATCH (d)-[:INDICATED_FOR]->(:Disease {name: 'obesity disorder'})\nRETURN DISTINCT d.name AS drug",
    ),
    (
        "What environmental exposures are linked to lung cancer?",
        "MATCH (e:Exposure)-[:LINKED_TO]->(:Disease {name: 'lung cancer'})\nRETURN DISTINCT e.name AS exposure LIMIT 50",
    ),
    (
        "Which diseases can cause seizures?",
        "MATCH (d:Disease)-[:HAS_PHENOTYPE]->(:Phenotype {name: 'Seizure'})\nRETURN DISTINCT d.name AS disease LIMIT 50",
    ),
    (
        "Which drugs can cause hair loss as a side effect?",
        "MATCH (d:Drug)-[:HAS_SIDE_EFFECT]->(:Phenotype {name: 'Alopecia'})\nRETURN DISTINCT d.name AS drug LIMIT 50",
    ),
    (
        "What pathways is BRCA1 involved in?",
        "MATCH (p:Pathway)-[:INVOLVES_GENE]->(:Gene {name: 'BRCA1'})\nRETURN DISTINCT p.name AS pathway LIMIT 50",
    ),
    (
        "Which of those drugs also have nausea as a side effect?",
        "// follow-up: the rewritten question names the earlier drugs explicitly\n"
        "MATCH (d:Drug)-[:HAS_SIDE_EFFECT]->(:Phenotype {name: 'Nausea'})\n"
        "WHERE d.name IN ['Metformin', 'Sitagliptin', 'Liraglutide']\nRETURN DISTINCT d.name AS drug",
    ),
    (
        "What are the subtypes of epilepsy?",
        "MATCH (:Disease {name: 'epilepsy'})-[:PARENT_CHILD]-(d:Disease)\nRETURN DISTINCT d.name AS related_disease LIMIT 50",
    ),
    (
        "Which genes are targeted by the most drugs indicated for rheumatoid arthritis?",
        "MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: 'rheumatoid arthritis'})\n"
        "MATCH (d)-[:TARGETS]->(g:Gene)\nRETURN g.name AS gene, count(DISTINCT d) AS drugs ORDER BY drugs DESC LIMIT 10",
    ),
]


@lru_cache
def _example_vectors() -> np.ndarray:
    vecs = np.array(get_embeddings().embed_documents([q for q, _ in EXAMPLES]), dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def select_examples(question: str, k: int = 4) -> list[tuple[str, str]]:
    q = np.array(get_embeddings().embed_query(question), dtype=np.float32)
    q /= np.linalg.norm(q)
    scores = _example_vectors() @ q
    return [EXAMPLES[i] for i in np.argsort(-scores)[:k]]
