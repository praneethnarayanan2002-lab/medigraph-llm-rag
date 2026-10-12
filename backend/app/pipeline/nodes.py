"""LangGraph node functions. Each takes the state and returns a partial update."""

from __future__ import annotations

import json
import time
from typing import Any

import neo4j.exceptions
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.graph.db import get_db
from app.graph.linker import link_entity
from app.graph.schema import NODE_LABELS, schema_prompt
from app.llm import get_chat_model
from app.pipeline import prompts
from app.pipeline.examples import select_examples
from app.pipeline.guards import extract_cypher, name_literals, validate_static
from app.pipeline.state import GraphRAGState

MAX_TEXT_CHARS = 500


def _llm(config: RunnableConfig | None):
    conf = (config or {}).get("configurable", {})
    return get_chat_model(conf.get("provider"), conf.get("model"))


def _trace(step: str, start: float, **detail: Any) -> list[dict[str, Any]]:
    return [{"step": step, "ms": round((time.perf_counter() - start) * 1000), **detail}]


# --- 1. contextualize -------------------------------------------------------------------------


def contextualize(state: GraphRAGState, config: RunnableConfig) -> dict:
    start = time.perf_counter()
    history = state.get("history") or []
    if not history:
        q = state["question"]
        return {"standalone_question": q, "trace": _trace("contextualize", start, rewritten=False)}

    convo = "\n".join(f"{t['role']}: {t['content'][:800]}" for t in history[-6:])
    reply = _llm(config).invoke(prompts.CONTEXTUALIZE.format(history=convo, question=state["question"]))
    q = reply.content.strip().strip('"') or state["question"]
    return {"standalone_question": q, "trace": _trace("contextualize", start, rewritten=True, question=q)}


# --- 2. understand: extract entities + link them to graph nodes ---------------------------------


class Mention(BaseModel):
    mention: str = Field(description="the exact text span from the question")
    canonical: str = Field(description="standard medical / database term for the entity")
    type: str = Field(description="Disease, Drug, Gene, Phenotype, Exposure, Anatomy or Pathway")


class Extraction(BaseModel):
    entities: list[Mention]
    in_scope: bool
    reason: str = ""


def understand(state: GraphRAGState, config: RunnableConfig) -> dict:
    start = time.perf_counter()
    question = state["standalone_question"]
    extractor = _llm(config).with_structured_output(Extraction)
    try:
        parsed: Extraction = extractor.invoke(prompts.EXTRACT.format(question=question))
    except Exception:  # malformed JSON from the model: carry on without entity hints
        parsed = Extraction(entities=[], in_scope=True)

    db = get_db()
    linked: list[dict[str, Any]] = []
    for m in parsed.entities[:10]:
        label = m.type if m.type in NODE_LABELS else None
        candidates = link_entity(db, m.canonical, label)
        if m.mention.lower() != m.canonical.lower():
            candidates += link_entity(db, m.mention, label)
        if not candidates and label:  # the model may have guessed the wrong type
            candidates = link_entity(db, m.canonical, None)
        if not candidates:
            continue
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        alternatives = [c.name for c in candidates[1:] if c.name != best.name][:2]
        linked.append({**best.as_dict(), "mention": m.mention, "alternatives": alternatives})

    return {
        "entities": linked,
        "in_scope": parsed.in_scope,
        "scope_reason": parsed.reason,
        "trace": _trace("understand", start, entities=linked, in_scope=parsed.in_scope),
    }


# --- 3. text2cypher ------------------------------------------------------------------------------


def _entities_block(entities: list[dict[str, Any]]) -> str:
    if not entities:
        return "(none found - rely on the schema)"
    lines = []
    for e in entities:
        line = f'- "{e["mention"]}" -> (:{e["label"]} {{name: "{e["name"]}"}})'
        if e.get("alternatives") and e["score"] < 0.95:
            line += "  [less likely: " + ", ".join(f'"{a}"' for a in e["alternatives"]) + "]"
        lines.append(line)
    return "\n".join(lines)


def generate_cypher(state: GraphRAGState, config: RunnableConfig) -> dict:
    start = time.perf_counter()
    question = state["standalone_question"]
    examples = "\n\n".join(f"Question: {q}\nCypher:\n{c}" for q, c in select_examples(question))
    system = prompts.TEXT2CYPHER.format(
        schema=schema_prompt(), entities=_entities_block(state.get("entities", [])), examples=examples
    )
    messages = [SystemMessage(system), HumanMessage(f"Question: {question}\nCypher:")]
    if state.get("errors") and state.get("cypher"):
        messages += [
            AIMessage(state["cypher"]),
            HumanMessage(
                prompts.TEXT2CYPHER_RETRY.format(cypher=state["cypher"], errors="\n".join(state["errors"]))
            ),
        ]
    reply = _llm(config).invoke(messages)
    cypher = extract_cypher(str(reply.content))
    attempt = state.get("attempts", 0) + 1
    return {
        "cypher": cypher,
        "attempts": attempt,
        "errors": [],
        "trace": _trace("generate_cypher", start, attempt=attempt, cypher=cypher),
    }


# --- 4. validate ---------------------------------------------------------------------------------


def check_entity_names(cypher: str) -> list[str]:
    """Every name literal in the query must be a real node; otherwise suggest the closest ones.
    Catches the model inventing plausible names (e.g. 'Bleeding' instead of 'Abnormal bleeding')."""
    db = get_db()
    errors = []
    for label, name in name_literals(cypher):
        labels = [label] if label in NODE_LABELS else list(NODE_LABELS)
        found = db.read(
            "\nUNION\n".join(f"MATCH (n:{lab} {{name: $name}}) RETURN 1 AS ok" for lab in labels),
            {"name": name},
            max_rows=1,
        ).rows
        if found:
            continue
        suggestions = [c.name for c in link_entity(db, name, label if label in NODE_LABELS else None)]
        hint = f" Closest existing names: {', '.join(repr(s) for s in suggestions)}." if suggestions else ""
        where = f":{label} " if label else ""
        errors.append(f"No {where}node is named '{name}'.{hint}")
    return errors


def validate(state: GraphRAGState) -> dict:
    start = time.perf_counter()
    cypher = state["cypher"]
    errors = validate_static(cypher)
    if not errors:
        try:
            query_type = get_db().explain(cypher)
            if query_type != "r":
                errors = [f"Query type '{query_type}' is not read-only."]
        except neo4j.exceptions.Neo4jError as exc:
            errors = [f"Neo4j rejected the query: {exc.message}"]
    if not errors:
        errors = check_entity_names(cypher)
    log = {"attempt": state.get("attempts", 0), "cypher": cypher, "errors": errors}
    return {
        "errors": errors,
        "attempt_log": [log],
        "trace": _trace("validate", start, ok=not errors, errors=errors),
    }


# --- 5. execute ----------------------------------------------------------------------------------


def execute(state: GraphRAGState) -> dict:
    start = time.perf_counter()
    try:
        result = get_db().read(state["cypher"])
    except neo4j.exceptions.Neo4jError as exc:
        errors = [f"Execution failed: {exc.message}"]
        return {"errors": errors, "rows": [], "trace": _trace("execute", start, errors=errors)}

    rows = result.rows
    update: dict[str, Any] = {
        "rows": rows,
        "truncated": result.truncated,
        "trace": _trace("execute", start, n_rows=len(rows), truncated=result.truncated),
    }
    if _is_empty(rows) and not state.get("empty_retry_done"):
        # One chance to fix a query that ran but found nothing (often a reversed edge or the
        # wrong one of two similar entity names). Returning the same query means "it's right".
        update["empty_retry_done"] = True
        update["errors"] = [
            "The query ran but returned no results. Check relationship directions and that the "
            "entity names match the linked entities. If the query is correct, return it unchanged."
        ]
    return update


def _is_empty(rows: list[dict[str, Any]]) -> bool:
    """No rows, or only rows whose values are all null / empty / zero-length lists."""
    return all(all(v in (None, "", [], {}) for v in row.values()) for row in rows)


# --- 6. fallback: neighbourhood of the linked entities --------------------------------------------


def fallback(state: GraphRAGState) -> dict:
    start = time.perf_counter()
    db = get_db()
    rows: list[dict[str, Any]] = []
    for e in state.get("entities", [])[:3]:
        label = e["label"]
        if label not in NODE_LABELS:  # labels are interpolated, so allow only known ones
            continue
        rows += db.read(
            f"""
            MATCH (n:{label} {{node_index: $idx}})-[r]-(m)
            WITH n, type(r) AS relationship, startNode(r) = n AS outgoing, labels(m)[0] AS other_label,
                 collect(DISTINCT m.name) AS names
            RETURN n.name AS entity, relationship,
                   CASE WHEN outgoing THEN 'outgoing' ELSE 'incoming' END AS direction,
                   other_label, size(names) AS total, names[..8] AS examples
            ORDER BY total DESC LIMIT 12
            """,
            {"idx": e["node_index"]},
        ).rows
        text = db.read(
            f"MATCH (n:{label} {{node_index: $idx}}) "
            "RETURN n.name AS entity, n.description AS description, n.mechanism AS mechanism, "
            "n.symptoms AS symptoms",
            {"idx": e["node_index"]},
        ).rows
        rows += [{k: v for k, v in r.items() if v} for r in text if r.get("description") or r.get("symptoms")]
    return {
        "rows": rows,
        "truncated": False,
        "used_fallback": True,
        "trace": _trace("fallback", start, n_rows=len(rows)),
    }


# --- 7. answer -----------------------------------------------------------------------------------


def _compact(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + "..."
    if isinstance(value, list):
        return [_compact(v) for v in value]
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items()}
    return value


def answer(state: GraphRAGState, config: RunnableConfig) -> dict:
    start = time.perf_counter()
    rows = _compact(state.get("rows", []))
    used_fallback = state.get("used_fallback", False)
    prompt = prompts.ANSWER.format(
        fallback_note=prompts.FALLBACK_NOTE if used_fallback else "",
        question=state["standalone_question"],
        cypher=state.get("cypher", "") if not used_fallback else "(neighbourhood lookup)",
        n_rows=len(rows),
        truncated=", truncated" if state.get("truncated") else "",
        rows=json.dumps(rows, ensure_ascii=False, indent=None, default=str)[:12000],
    )
    reply = _llm(config).invoke(prompt)
    return {
        "answer": str(reply.content).strip(),
        "status": "fallback" if used_fallback else "answered",
        "subgraph": build_subgraph(state),
        "trace": _trace("answer", start),
    }


def no_answer(state: GraphRAGState) -> dict:
    return {"answer": prompts.NO_ANSWER, "status": "no_answer", "subgraph": build_subgraph(state)}


def out_of_scope(state: GraphRAGState) -> dict:
    reason = state.get("scope_reason") or "the knowledge graph doesn't hold that kind of information"
    text = (
        f"I can't answer that from the knowledge graph: {reason.rstrip('.')}. "
        "I can tell you which drugs are indicated or contraindicated for a condition, their side "
        "effects and targets, or a disease's symptoms and associated genes. For personal medical "
        "decisions, please talk to a clinician."
    )
    return {"answer": text, "status": "out_of_scope", "subgraph": build_subgraph(state)}


# --- subgraph for the UI ---------------------------------------------------------------------------


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if len(value) <= 120 else []
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def build_subgraph(state: GraphRAGState, max_nodes: int = 60) -> dict[str, list[dict[str, Any]]]:
    """Nodes mentioned in the answer's evidence and the edges between them, for visualisation."""
    seeds = [e["name"] for e in state.get("entities", [])]
    names = list(dict.fromkeys(seeds + [s for row in state.get("rows", []) for s in _strings(row)]))
    names = names[: max_nodes * 2]
    if not names:
        return {"nodes": [], "edges": []}
    db = get_db()
    # One indexed lookup per label (labels come from the fixed schema, not from user input).
    by_label = "\nUNION ALL\n".join(
        f"MATCH (n:{label}) WHERE n.name IN $names "
        "RETURN elementId(n) AS id, n.name AS name, labels(n)[0] AS label, n.name IN $seeds AS seed"
        for label in NODE_LABELS
    )
    nodes = db.read(by_label, {"names": names, "seeds": seeds}, max_rows=max_nodes).rows
    ids = [n["id"] for n in nodes]
    edges = db.read(
        """
        MATCH (a)-[r]->(b) WHERE elementId(a) IN $ids AND elementId(b) IN $ids
        RETURN elementId(a) AS source, elementId(b) AS target, type(r) AS type
        """,
        {"ids": ids},
        max_rows=200,
    ).rows
    return {"nodes": nodes, "edges": edges}
