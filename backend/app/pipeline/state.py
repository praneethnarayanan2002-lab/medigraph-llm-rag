from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class Turn(TypedDict):
    role: str  # "user" | "assistant"
    content: str


class GraphRAGState(TypedDict, total=False):
    # input
    question: str
    history: list[Turn]
    # understanding
    standalone_question: str
    entities: list[dict[str, Any]]
    in_scope: bool
    scope_reason: str
    # text2cypher loop
    cypher: str
    attempts: int
    errors: list[str]
    attempt_log: Annotated[list[dict[str, Any]], operator.add]
    empty_retry_done: bool
    # retrieval
    rows: list[dict[str, Any]]
    truncated: bool
    used_fallback: bool
    # output
    answer: str
    status: str  # answered | fallback | no_answer | out_of_scope
    subgraph: dict[str, list[dict[str, Any]]]
    trace: Annotated[list[dict[str, Any]], operator.add]
