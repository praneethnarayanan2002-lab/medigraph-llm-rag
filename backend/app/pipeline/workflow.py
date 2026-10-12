"""The Graph RAG pipeline as a LangGraph state machine.

    contextualize -> understand -> generate_cypher -> validate -> execute -> answer
                         |              ^               |           |
                         |              +--- retry -----+-----------+   (invalid / empty, max N)
                         v                              v           v
                    out_of_scope                     fallback -> answer | no_answer
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.pipeline import nodes
from app.pipeline.state import GraphRAGState, Turn


def _after_understand(state: GraphRAGState) -> str:
    return "generate_cypher" if state.get("in_scope", True) else "out_of_scope"


def _after_validate(state: GraphRAGState) -> str:
    if not state.get("errors"):
        return "execute"
    return "generate_cypher" if state["attempts"] < get_settings().max_cypher_attempts else "fallback"


def _after_execute(state: GraphRAGState) -> str:
    if not state.get("errors"):
        return "fallback" if nodes._is_empty(state.get("rows", [])) else "answer"
    # Execution error, or an empty result we are allowed to retry once.
    return "generate_cypher" if state["attempts"] < get_settings().max_cypher_attempts else "fallback"


def _after_fallback(state: GraphRAGState) -> str:
    return "answer" if state.get("rows") else "no_answer"


def _after_generate(state: GraphRAGState) -> str:
    # An empty-result retry that returns the identical query: the model stands by it.
    log = state.get("attempt_log") or []
    if state.get("empty_retry_done") and log and log[-1]["cypher"] == state["cypher"]:
        return "fallback"
    return "validate"


@lru_cache
def build_workflow():
    g = StateGraph(GraphRAGState)
    g.add_node("contextualize", nodes.contextualize)
    g.add_node("understand", nodes.understand)
    g.add_node("generate_cypher", nodes.generate_cypher)
    g.add_node("validate", nodes.validate)
    g.add_node("execute", nodes.execute)
    g.add_node("fallback", nodes.fallback)
    g.add_node("answer", nodes.answer)
    g.add_node("no_answer", nodes.no_answer)
    g.add_node("out_of_scope", nodes.out_of_scope)

    g.add_edge(START, "contextualize")
    g.add_edge("contextualize", "understand")
    g.add_conditional_edges("understand", _after_understand, ["generate_cypher", "out_of_scope"])
    g.add_conditional_edges("generate_cypher", _after_generate, ["validate", "fallback"])
    g.add_conditional_edges("validate", _after_validate, ["execute", "generate_cypher", "fallback"])
    g.add_conditional_edges("execute", _after_execute, ["answer", "generate_cypher", "fallback"])
    g.add_conditional_edges("fallback", _after_fallback, ["answer", "no_answer"])
    for terminal in ("answer", "no_answer", "out_of_scope"):
        g.add_edge(terminal, END)
    return g.compile()


def run(
    question: str,
    history: list[Turn] | None = None,
    provider: str | None = None,
    model: str | None = None,
    fast_model: str | None = None,
) -> dict[str, Any]:
    """`model` overrides the main model; `fast_model` the easy-step model. Overriding only `model`
    uses it for every step (so a baseline run tests one model end to end)."""
    conf = {"provider": provider, "model": model, "fast_model": fast_model}
    config = {"configurable": conf, "recursion_limit": 30}
    return build_workflow().invoke({"question": question, "history": history or []}, config)
