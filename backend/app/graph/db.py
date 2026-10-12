"""Thin Neo4j client: every query the LLM writes runs in a READ-mode, time-limited transaction."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import neo4j
from neo4j.graph import Node, Path, Relationship

from app.config import get_settings

HIDDEN_PROPS = {"embedding"}


@dataclass
class QueryResult:
    rows: list[dict[str, Any]]
    truncated: bool


def _to_plain(value: Any) -> Any:
    if isinstance(value, Node):
        props = {k: v for k, v in value.items() if k not in HIDDEN_PROPS}
        return {"labels": sorted(value.labels), **props}
    if isinstance(value, Relationship):
        return {"type": value.type, **dict(value.items())}
    if isinstance(value, Path):
        return [_to_plain(n) for n in value.nodes]
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items() if k not in HIDDEN_PROPS}
    return value


class GraphDB:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self.driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def read(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
        max_rows: int | None = None,
        timeout: float | None = None,
    ) -> QueryResult:
        """Run a query in READ access mode. Neo4j itself rejects writes in this mode, so this is
        a server-enforced guard on top of the static checks in `pipeline/guards.py`."""
        s = get_settings()
        max_rows = max_rows or s.max_result_rows
        query = neo4j.Query(cypher, timeout=timeout or s.query_timeout_s)
        rows: list[dict[str, Any]] = []
        truncated = False
        with self.driver.session(default_access_mode=neo4j.READ_ACCESS) as session:
            result = session.run(query, params or {})
            for record in result:
                if len(rows) >= max_rows:
                    truncated = True
                    break
                rows.append({k: _to_plain(v) for k, v in record.items()})
            result.consume()
        return QueryResult(rows=rows, truncated=truncated)

    def explain(self, cypher: str) -> str:
        """Plan the query without running it. Raises on syntax / semantic errors and
        returns Neo4j's query type: 'r' (read), 'rw', 'w' or 's' (schema)."""
        with self.driver.session(default_access_mode=neo4j.READ_ACCESS) as session:
            summary = session.run("EXPLAIN " + cypher).consume()
            return summary.query_type

    def write(self, cypher: str, params: dict[str, Any] | None = None) -> None:
        """Only used by offline setup scripts (indexes, embeddings), never by the pipeline."""
        with self.driver.session() as session:
            session.run(cypher, params or {}).consume()


@lru_cache
def get_db() -> GraphDB:
    s = get_settings()
    return GraphDB(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
