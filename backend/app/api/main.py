"""HTTP API. `POST /api/chat` streams the pipeline as Server-Sent Events so the UI can show each
step (entities, Cypher, retries, rows) as it happens and the answer token by token.

Run: uv run uvicorn app.api.main:app --app-dir backend --port 8000
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.graph.db import get_db
from app.graph.schema import RELATIONSHIPS, schema_prompt
from app.pipeline.workflow import build_workflow

app = FastAPI(title="MediGraph", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STEP_EVENTS = {"contextualize", "understand", "generate_cypher", "validate", "execute", "fallback"}
FINAL_NODES = {"answer", "no_answer", "out_of_scope"}


class TurnIn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class ChatIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    history: list[TurnIn] = Field(default_factory=list, max_length=20)


def _event(name: str, data: Any) -> dict[str, str]:
    return {"event": name, "data": json.dumps(data, default=str)}


@app.get("/api/health")
def health() -> dict[str, Any]:
    s = get_settings()
    try:
        counts = get_db().read("MATCH (n) RETURN count(n) AS nodes").rows[0]
        neo4j_ok = True
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a status
        counts, neo4j_ok = {"error": str(exc)}, False
    return {"neo4j": neo4j_ok, "graph": counts, "llm": f"{s.llm_provider}:{s.ollama_model}"}


@app.get("/api/schema")
def schema() -> dict[str, Any]:
    return {"text": schema_prompt(), "relationships": [r[:3] for r in RELATIONSHIPS]}


@app.get("/api/examples")
def examples() -> list[str]:
    return [
        "What drugs treat a heart attack?",
        "What are the side effects of metformin?",
        "Which genes are associated with cystic fibrosis?",
        "Which drugs target genes linked to Alzheimer disease?",
        "Is ibuprofen contraindicated in peptic ulcer disease?",
        "What environmental exposures are linked to asthma?",
    ]


@app.post("/api/chat")
async def chat(body: ChatIn) -> EventSourceResponse:
    if not body.question.strip():
        raise HTTPException(400, "Empty question")
    workflow = build_workflow()
    inputs = {"question": body.question, "history": [t.model_dump() for t in body.history]}

    async def stream():
        start = time.perf_counter()
        final: dict[str, Any] = {}
        try:
            async for mode, chunk in workflow.astream(
                inputs, {"recursion_limit": 30}, stream_mode=["updates", "messages"]
            ):
                if mode == "messages":
                    message, meta = chunk
                    if meta.get("langgraph_node") == "answer" and message.content:
                        yield _event("token", {"text": message.content})
                    continue
                for node, update in chunk.items():
                    if not update:
                        continue
                    if node in STEP_EVENTS:
                        for t in update.get("trace", []):
                            yield _event("step", t)
                        if node == "execute" and "rows" in update:
                            rows = {"rows": update["rows"][:50], "truncated": update.get("truncated")}
                            yield _event("rows", rows)
                        if node == "fallback":
                            yield _event("rows", {"rows": update["rows"], "fallback": True})
                    if node in FINAL_NODES:
                        final = update
            yield _event(
                "final",
                {
                    "answer": final.get("answer", ""),
                    "status": final.get("status"),
                    "subgraph": final.get("subgraph", {"nodes": [], "edges": []}),
                    "ms": round((time.perf_counter() - start) * 1000),
                },
            )
        except Exception as exc:  # noqa: BLE001 - report pipeline failures to the client
            yield _event("error", {"message": str(exc)})

    return EventSourceResponse(stream())
