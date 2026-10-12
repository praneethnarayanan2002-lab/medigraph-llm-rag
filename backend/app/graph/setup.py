"""One-off database setup after the bulk import: indexes + name embeddings for entity linking.

Usage: uv run python -m app.graph.setup [--skip-embeddings]
"""

from __future__ import annotations

import argparse
import time

from tqdm import tqdm

from app.graph.db import get_db
from app.graph.linker import FULLTEXT_INDEX, VECTOR_INDEX_PREFIX
from app.graph.schema import EMBEDDED_LABELS, LINKABLE_LABELS, NODE_LABELS
from app.llm import get_embeddings

BATCH = 64


def _embed_with_retry(emb, texts: list[str], tries: int = 4) -> list[list[float]]:
    # The Ollama embedding runner occasionally drops a connection under sustained load.
    for attempt in range(tries):
        try:
            return emb.embed_documents(texts)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def create_indexes() -> None:
    db = get_db()
    for label in NODE_LABELS:
        db.write(f"CREATE INDEX {label.lower()}_name IF NOT EXISTS FOR (n:{label}) ON (n.name)")
        db.write(f"CREATE INDEX {label.lower()}_idx IF NOT EXISTS FOR (n:{label}) ON (n.node_index)")
    labels = "|".join(LINKABLE_LABELS)
    db.write(
        f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS FOR (n:{labels}) ON EACH [n.name]"
    )
    db.write("CALL db.awaitIndexes(300)")
    print("range + full-text indexes ready")


def embed_names(dimensions: int) -> None:
    db = get_db()
    emb = get_embeddings()
    for label in EMBEDDED_LABELS:
        todo = db.read(
            f"MATCH (n:{label}) WHERE n.embedding IS NULL RETURN n.node_index AS idx, n.name AS name",
            max_rows=10**6,
        ).rows
        for i in tqdm(range(0, len(todo), BATCH), desc=f"embedding {label}", unit="batch"):
            chunk = todo[i : i + BATCH]
            vectors = _embed_with_retry(emb, [r["name"] for r in chunk])
            db.write(
                f"""
                UNWIND $rows AS row
                MATCH (n:{label} {{node_index: row.idx}})
                CALL db.create.setNodeVectorProperty(n, 'embedding', row.vec)
                """,
                {"rows": [{"idx": r["idx"], "vec": v} for r, v in zip(chunk, vectors, strict=False)]},
            )
        db.write(
            f"""
            CREATE VECTOR INDEX {VECTOR_INDEX_PREFIX}{label.lower()} IF NOT EXISTS
            FOR (n:{label}) ON (n.embedding)
            OPTIONS {{indexConfig: {{`vector.dimensions`: {dimensions},
                                    `vector.similarity_function`: 'cosine'}}}}
            """
        )
    db.write("CALL db.awaitIndexes(600)")
    print("vector indexes ready")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-embeddings", action="store_true")
    args = parser.parse_args()

    start = time.time()
    create_indexes()
    if not args.skip_embeddings:
        dims = len(get_embeddings().embed_query("test"))
        embed_names(dims)
    print(f"done in {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
