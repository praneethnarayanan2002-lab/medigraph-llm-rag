"""Entity linking: map a user's phrase ("heart attack") to concrete graph nodes
("myocardial infarction"). Two signals are combined:
  * full-text (Lucene) search on node names, good for exact / spelling-variant matches
  * vector search on name embeddings, good for synonyms and lay terms
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

import neo4j.exceptions
import numpy as np

from app.config import get_settings
from app.graph.db import GraphDB
from app.graph.schema import EMBEDDED_LABELS, LINKABLE_LABELS
from app.llm import get_entity_embeddings

FULLTEXT_INDEX = "entity_names"
# Vector property / index names per entity embedder.
VECTOR_PROPERTY = {"nomic": "embedding", "sapbert": "sapbert"}


def vector_index_name(label: str, kind: str) -> str:
    return f"{VECTOR_PROPERTY[kind]}_{label.lower()}"

# Blended-score floor per embedder (cosine scales differ: SapBERT spreads scores much wider).
MIN_SCORE = {"nomic": 0.75, "sapbert": 0.55}
LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


@dataclass
class LinkedEntity:
    mention: str
    label: str
    name: str
    node_index: int
    score: float
    method: str

    def as_dict(self) -> dict:
        return asdict(self)


def _lucene_escape(text: str) -> str:
    return LUCENE_SPECIAL.sub(r"\\\1", text)


def fulltext_candidates(db: GraphDB, mention: str, label: str | None, k: int) -> list[LinkedEntity]:
    terms = [t for t in _lucene_escape(mention).split() if t]
    if not terms:
        return []
    # Exact phrase scores highest; fuzzy terms catch typos ("diabetis").
    query = f'"{" ".join(terms)}"^3 OR (' + " AND ".join(f"{t}~1" if len(t) > 4 else t for t in terms) + ")"
    rows = db.read(
        """
        CALL db.index.fulltext.queryNodes($index, $query) YIELD node, score
        WHERE $label IS NULL OR $label IN labels(node)
        RETURN node.name AS name, node.node_index AS idx, labels(node)[0] AS label
        LIMIT $k
        """,
        {"index": FULLTEXT_INDEX, "query": query, "label": label, "k": k},
    ).rows
    return [LinkedEntity(mention, r["label"], r["name"], r["idx"], 0.0, "fulltext") for r in rows]


def vector_candidates(
    db: GraphDB, mention: str, vector: list[float], label: str | None, k: int
) -> list[LinkedEntity]:
    kind = get_settings().entity_embedder
    available = EMBEDDED_LABELS[kind]
    labels = [label] if label in available else ([] if label else available)
    out: list[LinkedEntity] = []
    for lab in labels:
        try:
            rows = db.read(
                """
                CALL db.index.vector.queryNodes($index, $k, $vector) YIELD node, score
                RETURN node.name AS name, node.node_index AS idx, score
                """,
                {"index": vector_index_name(lab, kind), "k": k, "vector": vector},
            ).rows
        except neo4j.exceptions.ClientError:  # index not built yet: degrade to full-text only
            continue
        out += [LinkedEntity(mention, lab, r["name"], r["idx"], r["score"], "vector") for r in rows]
    return out


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def link_entity(db: GraphDB, mention: str, label: str | None = None) -> list[LinkedEntity]:
    """Return the best few candidate nodes for one mention, best first.

    Candidates come from full-text and vector search, then all are re-scored on one scale:
    semantic similarity (cosine of name embeddings) blended with lexical similarity, so a fuzzy
    full-text hit ("diabetis" -> "diabetic ketoacidosis") can't outrank a close synonym.
    Candidates below MIN_SCORE are dropped rather than guessed.
    """
    k = get_settings().entity_candidates
    if label not in LINKABLE_LABELS:
        label = None

    # Exact name match via the per-label name indexes, trying common casings
    # (diseases are lower-case in PrimeKG, drugs capitalised, genes upper-case).
    variants = list(dict.fromkeys([mention, mention.lower(), mention.capitalize(), mention.upper()]))
    labels = [label] if label else LINKABLE_LABELS
    exact = db.read(
        "\nUNION\n".join(
            f"MATCH (n:{lab}) WHERE n.name IN $names "
            f"RETURN n.name AS name, n.node_index AS idx, '{lab}' AS label"
            for lab in labels
        ),
        {"names": variants},
        max_rows=3,
    ).rows
    if exact:
        return [LinkedEntity(mention, r["label"], r["name"], r["idx"], 1.0, "exact") for r in exact]

    emb = get_entity_embeddings()
    vector = emb.embed_query(mention)
    candidates: dict[int, LinkedEntity] = {}
    for c in fulltext_candidates(db, mention, label, k) + vector_candidates(db, mention, vector, label, k):
        if c.node_index in candidates:
            candidates[c.node_index].method = "fulltext+vector"
        else:
            candidates[c.node_index] = c
    if not candidates:
        return []

    ranked = list(candidates.values())
    name_vectors = emb.embed_documents([c.name for c in ranked])
    q = np.array(vector)
    for c, v in zip(ranked, name_vectors, strict=False):
        semantic = _cosine(q, np.array(v))
        lexical = SequenceMatcher(None, mention.lower(), c.name.lower()).ratio()
        c.score = round(0.85 * semantic + 0.15 * lexical, 3)
    floor = MIN_SCORE[get_settings().entity_embedder]
    ranked = [c for c in ranked if c.score >= floor]
    ranked.sort(key=lambda e: e.score, reverse=True)
    return ranked[:k]
