# Design decisions

Every significant choice in MediGraph, why I made it, what else I considered, and what it cost.
Where I measured something, the numbers are included. Decisions are grouped by area and numbered
so other docs can refer to them (e.g. "see D12").

Format for each decision:

- **Decision**: what I chose
- **Why**: the reasoning
- **Alternatives**: what else I considered and why not
- **Trade-off**: what this choice costs
- **Evidence**: measurements, where I have them

---

## Contents

1. [Problem framing](#1-problem-framing) (D1–D3)
2. [Data](#2-data) (D4–D7)
3. [Database](#3-database) (D8–D11)
4. [Models](#4-models) (D12–D19)
5. [Pipeline design](#5-pipeline-design) (D20–D32)
6. [Application and tooling](#6-application-and-tooling) (D33–D39)
7. [Evaluation](#7-evaluation) (D40–D46)
8. [Operations](#8-operations) (D47–D49)
9. [Summary of trade-offs](#9-summary-of-trade-offs)
10. [Decisions I'd revisit](#10-decisions-id-revisit)

---

## 1. Problem framing

### D1. Answer from retrieved facts, never from the model's memory

- **Decision**: Every answer is generated only from rows returned by a database query. The model is
  never asked a medical question directly.
- **Why**: An LLM's built-in medical knowledge is frozen at its training cutoff, weak on rare
  diseases, has no sources, and is mixed with hallucination. In medicine, a fluent wrong answer is
  worse than no answer. Retrieval makes every claim traceable to a specific fact.
- **Alternatives**:
  - *Ask the model directly*: simplest, but this is exactly the failure I wanted to fix.
  - *Fine-tune a medical model*: still answers from memory, still unsourced, expensive to update.
- **Trade-off**: The system can only answer what the graph contains. "What treats cancer?" returns
  the three drugs PrimeKG lists, not what an oncologist would say.
- **Evidence**: Same model (Qwen3.5-9B) without the graph: 19.8% answer recall and 39.1% of named
  entities unsupported. With the graph: 96.8% recall and 0% unsupported (147 questions).

### D2. Knowledge graph instead of a vector store over documents

- **Decision**: Retrieve from a knowledge graph with explicit typed relationships.
- **Why**: The questions I cared about are relational: "which drugs treat X", "which genes are
  linked to Y", "which of *those* drugs also cause Z", "how many". A graph stores those
  relationships directly, so multi-hop questions are traversals and counts are exact. Each fact is
  one edge with its subject and object explicit, so there's no chunking problem.
- **Alternatives**:
  - *Vector RAG over drug labels or papers*: good for "explain how X works", weak for counting,
    filtering, intersections and "which of those". The model would have to count across chunks that
    may not fit in the prompt.
  - *Hybrid graph + vectors*: better coverage, but more moving parts. Listed as a future improvement.
- **Trade-off**: Questions outside the schema (dosing, mechanisms in depth, guidelines) can't be
  answered from structure alone. The graph only knows what it was built with.
- **Evidence**: Count and yes/no questions: 100% correct with the graph vs 50% without (v2).

### D3. Local-first

- **Decision**: Everything runs on my laptop: the LLM (Ollama), the database (Docker), the API and UI.
- **Why**: Medical questions can be sensitive, so nothing leaves the machine. Running locally is
  free, so I could re-run the full 147-question evaluation many times. And it forced me to make a
  9B model work well, which turned out to be mostly about the pipeline, not the model.
- **Alternatives**: A hosted API model (faster, stronger) or a cloud GPU.
- **Trade-off**: Speed. About 37 s per question (median) on a 16 GB Mac, almost all of it LLM time.
  The model provider is a config switch (D17), so this can change without code changes.

---

## 2. Data

### D4. PrimeKG as the knowledge source

- **Decision**: Use PrimeKG (Chandak, Huang & Zitnik, Scientific Data 2023).
- **Why**: It's built for precision medicine, integrates ~20 curated sources (DrugBank, SIDER,
  DisGeNET, MONDO, HPO, GO, Reactome and others), is disease-centred (17,080 diseases), has the
  clinical relationships I needed (indications, contraindications, side effects, targets,
  phenotypes, gene associations), includes text descriptions for drugs and diseases, is openly
  licensed (CC BY 4.0) and downloadable in one step.
- **Alternatives**:
  - *Hetionet*: older, smaller, fewer clinical relationships.
  - *SPOKE*: larger but access is more involved.
  - *UMLS / SNOMED*: vocabularies with licensing restrictions, not a ready-made relationship graph.
  - *Build my own from papers with an LLM*: extraction errors would be baked into the data, which
    defeats the purpose of grounding.
- **Trade-off**: A 2023 snapshot. New drugs and indications are missing. Some disease concepts are
  grouped or duplicated (e.g. "hypertension" and "hypertensive disorder" both exist).

### D5. Use the full graph, not a subset

- **Decision**: Load all 129,375 nodes and 4,050,249 relationships.
- **Why**: Multi-hop questions can need any part of the graph (disease → gene → drug). Neo4j handles
  this size easily, and bulk import takes seconds (D7).
- **Alternatives**: A clinical subset (~130K nodes, ~370K relationships) would fit Neo4j AuraDB's free
  tier for cloud hosting.
- **Trade-off**: More disk and memory; Neo4j uses ~2 GB. The subset remains an option for hosting.

### D6. Reshape PrimeKG before loading

- **Decision**: In `data/scripts/build_import.py`:
  1. Map node types to clean labels (`gene/protein` → `Gene`).
  2. Rename relations to readable verbs (`indication` → `INDICATED_FOR`, `drug_effect` →
     `HAS_SIDE_EFFECT`); split `drug_protein` into `TARGETS`, `METABOLIZED_BY`, `TRANSPORTED_BY`,
     `CARRIED_BY` using PrimeKG's `display_relation` column.
  3. Orient every edge from the lower-ranked label to the higher (Drug → Disease → ... → Gene), and
     for same-label edges from the smaller id to the larger, then drop duplicates.
  4. Attach drug `description`/`mechanism` and disease `description`/`symptoms` text, cut to 700
     characters.
- **Why**: Raw PrimeKG stores every edge twice with generic names. An LLM writes much better Cypher
  against a schema that reads like English with consistent directions, and a consistent direction
  is what lets the validator check direction at all (D26). Text properties let the system answer
  "what is X" and "how does X work".
- **Alternatives**: Load raw PrimeKG and use undirected matches everywhere. Simpler import, but
  ambiguous for the model and impossible to validate direction.
- **Trade-off**: Some relations are genuinely undirected (drug–drug, gene–gene, hierarchy). They're
  stored one way and marked "undirected" in the schema; queries must match them without an arrow.
- **Evidence**: After de-duplication the edge count is exactly 4,050,249, the number reported in the
  PrimeKG paper. That match validated the whole transformation.

### D7. Bulk import with `neo4j-admin`

- **Decision**: Write CSVs with Neo4j import headers and load them with
  `neo4j-admin database import full` (the `make import` target).
- **Why**: It builds the store files directly, offline. 4M relationships load in ~3 seconds.
- **Alternatives**: `LOAD CSV` or batched `MERGE` from Python: minutes to hours, and more code.
- **Trade-off**: Only works into an empty database (it overwrites). Fine for a static dataset;
  incremental updates would need a different path.

---

## 3. Database

### D8. Neo4j as the graph database

- **Decision**: Neo4j 5.26 Community.
- **Why**: Mature, easy to run locally in Docker, Cypher is readable for both people and LLMs (lots
  of Cypher in LLM training data), and it has built-in full-text (Lucene) and vector (HNSW) indexes,
  so entity linking needs no second system. `EXPLAIN` exposes the query type, which became a key
  safety check (D26).
- **Alternatives**:
  - *Memgraph*: Cypher-compatible and fast, smaller ecosystem.
  - *Amazon Neptune*: managed only, not local.
  - *An RDF store with SPARQL*: standards-based, but SPARQL is harder for small models.
  - *PostgreSQL + pgvector*: multi-hop questions become chains of joins.
- **Trade-off**: Community edition has no role-based users, so I can't create a database user that
  is physically read-only. READ access-mode sessions plus static checks cover this instead (D26).

### D9. Run Neo4j in Docker with fixed memory

- **Decision**: `docker-compose.yml` with 1 GB heap, 1 GB page cache, a 15 s server-side transaction
  timeout, and a named volume for data.
- **Why**: Reproducible setup with one command, no local install, data survives restarts. The
  memory numbers are enough for this graph and leave room for the LLM on a 16 GB laptop.
- **Trade-off**: Docker Desktop on macOS runs a VM that reserves memory up front (D47).

### D10. Indexes: range per label, one full-text index, vector indexes per label

- **Decision**: In `backend/app/graph/setup.py`: a range index on `name` and on `node_index` for every
  label; one full-text index `entity_names` over the 7 linkable labels; one HNSW vector index per
  embedded label (`sapbert_disease`, `sapbert_drug`, ...).
- **Why**: Exact name lookups must be instant and only work with a label (`MATCH (n:Disease {name:
  ...})`). Full-text handles typos and partial names. Vector indexes handle synonyms.
- **Trade-off**: Indexes use disk and slow down writes; irrelevant here since the data is static.

### D11. Store embeddings in Neo4j, no separate vector database

- **Decision**: SapBERT vectors are stored as a property on each node with Neo4j vector indexes.
- **Why**: The things being searched are graph nodes. A vector hit is directly a node that can go
  into a Cypher query. One system, nothing to keep in sync. ~85,000 vectors is small.
- **Alternatives**: Qdrant, Chroma, FAISS, pgvector. All would work, all add a second store and a
  mapping from vector ids back to nodes.
- **Trade-off**: Neo4j's vector search has fewer tuning options than a dedicated vector DB. Not a
  limitation at this scale.

---

## 4. Models

### D12. Serve the LLM locally with Ollama

- **Decision**: Ollama on `localhost:11434`, called through LangChain's `ChatOllama`.
- **Why**: One-command model downloads, quantized GGUF models, Apple Metal GPU support, a simple HTTP
  API, and it keeps the model loaded between calls.
- **Alternatives**: llama.cpp directly (more control, more setup), vLLM (great on NVIDIA GPUs, not
  for a Mac), MLX (fast on Apple Silicon, less tooling around it).
- **Trade-off**: Less control over batching and cache slots. Setting `OLLAMA_NUM_PARALLEL` to give
  each prompt type its own cache slot didn't take effect in this Ollama version.

### D13. Qwen3.5-9B as the main model

- **Decision**: `qwen3.5:9b` for rewriting, entity extraction, Cypher writing and answering.
- **Why**: The strongest open model that fits (~6 GB at 4-bit) next to Neo4j on 16 GB. Apache 2.0
  license. Good at code-like output and supports structured output.
- **Alternatives**:
  - *Llama 3.1 8B*: already installed; used as the comparison baseline.
  - *gpt-oss-20b* (MoE): stronger reasoning, but needs ~12–14 GB, too much alongside Neo4j.
  - *Gemma 4 E4B*: smaller, weaker at code.
  - *MedGemma*: medical tuning doesn't help here, because medical knowledge comes from the graph; the
    model only needs to write queries and stay faithful.
  - *Neo4j's fine-tuned Text2Cypher models*: specialised, but not a drop-in for the other steps.
- **Trade-off**: ~10 tokens/s on this laptop, so each LLM step takes 10–30 s.
- **Evidence**: On the same 65 questions, Qwen3.5-9B got 96.0% exact result match vs 76.0% for Llama
  3.1 8B, and never needed the fallback (Llama: 23%). Llama was faster (21 s vs 37 s median), partly
  because it ran on a less loaded machine.

### D14. 4-bit quantization (Q4_K_M)

- **Decision**: Use Ollama's default Q4_K_M build.
- **Why**: ~4.85 bits per weight cuts memory from ~18 GB (16-bit) to ~6 GB with little quality loss.
  A bigger model at 4-bit usually beats a smaller one at 16-bit for the same memory.
- **Trade-off**: Small quality loss vs full precision; not measured separately.

### D15. Generation settings: temperature 0, 8K context, thinking off, keep-alive 30 min

- **Decision**: In `backend/app/llm.py`: `temperature=0`, `num_ctx=8192`, `reasoning=False`,
  `keep_alive="30m"`.
- **Why**:
  - *Temperature 0*: Cypher has one right answer, and evaluation needs repeatable outputs to compare
    versions fairly.
  - *8K context*: prompts are 1–3K tokens; a larger window grows the KV cache and memory use.
  - *Thinking off*: Qwen3.5's reasoning mode writes hundreds of tokens before answering, adding tens
    of seconds per call. The pipeline already breaks the problem into small guided steps.
  - *Keep-alive*: avoid reloading 6 GB of weights between questions.
- **Trade-off**: Greedy decoding can't "try again differently" without a changed prompt; that's why
  retries include the error message (D27).

### D16. No small "fast model" for easy steps (tested and reverted)

- **Decision**: Use the 9B model for every step. Model routing exists in code
  (`ollama_fast_model`), off by default.
- **Why**: I tried routing follow-up rewriting and entity extraction to `qwen3.5:2b`. On 16 GB of
  unified memory, having two models loaded increased swapping, and every step got slower (65–88 s per
  question vs ~45 s). The 2B model also ignored the rule to skip generic words like "drugs".
- **Trade-off**: Kept the simpler, faster setup. Routing could make sense on a machine with more memory.

### D17. Keep an OpenAI provider option

- **Decision**: `LLM_PROVIDER=ollama|openai` in `.env`; `get_chat_model()` returns `ChatOllama` or
  `ChatOpenAI`.
- **Why**: Lets the same pipeline be compared against an API model, or deployed with one later,
  without code changes.
- **Trade-off**: None at runtime; the OpenAI path isn't part of the main evaluation.

### D18. SapBERT for entity-linking embeddings

- **Decision**: `cambridgeltl/SapBERT-from-PubMedBERT-fulltext` from Hugging Face, [CLS] pooling,
  run with PyTorch on the Mac GPU (MPS).
- **Why**: Linking needs "same concept", not "related topic". SapBERT was trained on UMLS synonym
  sets so all names of one concept embed close together. A general embedder put "heart attack"
  closer to "heart failure" than to "myocardial infarction".
- **Alternatives**: nomic-embed-text (general, already installed), BioBERT/PubMedBERT without
  synonym training, a UMLS-based synonym dictionary (licensing).
- **Trade-off**: Adds PyTorch and Transformers (~650 MB in the environment). Brand names (Tylenol)
  aren't in its training synonyms, so the LLM's suggested generic name is still needed (D23).
- **Evidence**: 30 lay terms, vector search only: SapBERT 57% top-1 / 80% top-5 vs nomic 27% / 57%
  (`eval/link_eval.py`).

### D19. nomic-embed-text for few-shot example retrieval

- **Decision**: Use nomic (via Ollama) to pick the most similar example questions, with the
  `clustering:` prefix on both sides.
- **Why**: Comparing a question with example questions is general sentence similarity, which nomic
  handles well; SapBERT is tuned for short entity names. The `clustering:` prefix is nomic's
  recommended mode for symmetric comparison (question vs question).
- **Trade-off**: A second embedding model, but it's tiny (~300 MB) and already installed.

---

## 5. Pipeline design

### D20. Text-to-Cypher as the main retrieval method

- **Decision**: The LLM writes a Cypher query for each question.
- **Why**: Handles open-ended combinations (two hops, intersections, counts, yes/no) without
  anticipating every question type. Validation and retries (D26–D28) recover most of the reliability
  that fixed templates would give.
- **Alternatives**:
  - *Intent classification + query templates*: very reliable, but only covers question types I wrote
    templates for.
  - *Tool calling* (`drugs_for(disease)` functions): safe, same coverage limit as templates.
  - *Subgraph retrieval* (fetch neighbourhoods, verbalize edges): no query writing, but neighbourhoods
    explode around hub nodes and counting is unreliable. Used only as the fallback (D29).
  - *LangChain's `GraphCypherQAChain`*: one opaque call, no place for entity linking, validation,
    retries or per-step measurement.
- **Trade-off**: Queries can be valid but subtly wrong (e.g. including off-label uses for "treated
  with"). Only the evaluation catches those.
- **Evidence**: 94.8% of queries passed every check on the first try in v2.

### D21. A hand-written schema description

- **Decision**: `backend/app/graph/schema.py` defines labels, properties and directed relationship
  patterns, each with a one-line meaning. `schema_prompt()` renders it for the prompt, and the same
  structures drive the validator.
- **Why**: Automatic introspection (e.g. APOC `meta.data`) lists types but not what they *mean* or the
  intended direction. "INDICATED_FOR = approved treatment" vs "OFF_LABEL_USE_FOR" is exactly the
  distinction the model needs. Sharing the structures means prompt and checks can't drift apart.
- **Trade-off**: Must be updated by hand if the graph changes.

### D22. Dynamic few-shot with 22 hand-written examples, top 4 per question

- **Decision**: `backend/app/pipeline/examples.py` has 22 verified question → Cypher pairs covering
  every pattern. The 4 most similar to the current question go into the prompt.
- **Why**: Small models copy patterns well. Choosing the closest examples gives the best template for
  each question within a short prompt. Hand-written means every example is a correct, runnable query.
- **Alternatives**: Fixed examples (wastes prompt space on irrelevant patterns), many examples (longer,
  slower prompts), no examples (more direction and pattern mistakes).
- **Trade-off**: Example entities must be excluded from the test set to avoid leakage (D42).

### D23. One structured LLM call for entities, standard terms and scope

- **Decision**: `nodes.understand` asks for a JSON object (Pydantic `Extraction`): each mention, a
  `canonical` standard term, an entity type, plus `in_scope` and a reason.
- **Why**: The LLM's general knowledge is good at "Tylenol = Acetaminophen" and "Lou Gehrig's disease
  = ALS", which embeddings miss. Combining extraction and scope in one call saves a full LLM round
  trip (~10–30 s). Structured output with constrained decoding guarantees parseable JSON.
- **Trade-off**: This step is the slowest (often 10–35 s). A parsing failure falls back to "no
  entities, in scope" rather than failing the request.

### D24. Linking order and scoring

- **Decision**: In `backend/app/graph/linker.py`:
  1. Exact name match via range indexes, trying common casings (diseases are lower-case in PrimeKG,
     drugs capitalised, genes upper-case).
  2. Otherwise: candidates from Lucene full-text (fuzzy `~1` on words longer than 4 letters, exact
     phrase boosted) and SapBERT vector search.
  3. Re-score every candidate on one scale: `0.85 × cosine + 0.15 × string similarity`.
  4. Drop candidates below a floor (0.55 for SapBERT, 0.75 for nomic).
  5. In `nodes.understand`: if the user's own words match a node exactly, use them and ignore the
     LLM's suggested term; otherwise combine candidates for both.
- **Why**:
  - Exact first: cheapest and certain.
  - Hybrid search: full-text catches typos and codes, vectors catch synonyms.
  - Re-scoring: full-text and vector scores are on different scales; without re-scoring, a fuzzy hit
    like "diabetis" → "diabetic ketoacidosis" ranked as a perfect match.
  - Floors: better to link nothing than to answer about the wrong disease.
  - User's words win: in v1 the LLM "normalized" Stanolone acetate into Stanozolol (a different drug)
    and "Duane syndrome type 1" into the broader "Duane retraction syndrome".
- **Alternatives**: Reciprocal rank fusion (ignores score magnitudes), a cross-encoder reranker
  (slower, more models).
- **Trade-off**: Floors are hand-tuned per embedder. Re-scoring embeds each candidate name, a few
  extra milliseconds.

### D25. Rewrite follow-ups into standalone questions

- **Decision**: `nodes.contextualize` rewrites a follow-up using the last 6 turns ("Which of those
  cause nausea?" → "Which of Metformin, Sitagliptin, ... cause nausea?"). Every later step sees only
  the standalone question.
- **Why**: Keeps the other prompts short and history-free; entity linking and Cypher writing work on
  explicit names.
- **Alternatives**: Pass the whole history to every step (longer prompts, more confusion).
- **Trade-off**: One extra LLM call when there's history. Still fails when a follow-up refers to
  many items and the Cypher only covers the first (seen once in the eval, not fixed yet).

### D26. Layered query validation

- **Decision**: Before execution, `nodes.validate` runs:
  1. `guards.check_read_only`: rejects write and admin clauses (after blanking string literals), `CALL`
     except the two read-only index procedures, `CALL {}` subqueries, and multiple statements.
  2. `guards.check_schema`: unknown labels, relationship types and properties (with "did you mean"
     hints) and reversed edges (with the valid direction).
  3. `GraphDB.explain`: Neo4j plans the query; syntax errors surface, and only query type `r` passes.
  4. `nodes.check_entity_names`: every quoted name must exist as a node; otherwise suggest the
     closest real names via the linker.
  5. At execution, `GraphDB.read` uses a READ access-mode session, a 10 s timeout and a 50-row cap.
- **Why**: Defence in depth. Regexes can have gaps; EXPLAIN uses Neo4j's own parser; the READ
  session is enforced by the server no matter what the model wrote. The schema and name checks don't
  just block bad queries, they produce error messages good enough for the model to fix them.
- **Alternatives**: Only a regex (gaps), only the READ session (no useful feedback for retries),
  an Enterprise read-only user (needs a paid edition).
- **Trade-off**: ~0.2–0.9 s per validation; the regex checks need maintenance and tests (22 unit
  tests in `backend/tests/test_guards.py`).

### D27. Retry with error feedback, at most 3 attempts

- **Decision**: On validation or execution failure, `generate_cypher` is called again with the
  previous query and the exact errors as a conversation turn. Routing in `workflow.py` caps this at
  `max_cypher_attempts = 3`, then falls back.
- **Why**: Models fix their own mistakes well when told precisely what's wrong. A cap bounds latency
  and prevents loops.
- **Trade-off**: Each retry costs 10–30 s on this laptop.
- **Evidence**: Example: "What is Klinefelter syndrome treated with?" Attempt 1 reversed the edge; the
  validator returned the valid direction; attempt 2 was correct.

### D28. One retry for empty results, and "stand by it" detection

- **Decision**: If a valid query returns no rows, give the model one retry with a hint (check
  directions and entity names; return the query unchanged if it's right). If it returns the identical
  query, go straight to the fallback (`_after_generate`).
- **Why**: Empty results are often a subtle query mistake, but sometimes the answer genuinely isn't
  in the graph. One retry catches the first case without looping on the second.
- **Trade-off**: One extra LLM call on genuinely empty questions.

### D29. Neighbourhood fallback, and a fixed "not found" message

- **Decision**: When the exact query keeps finding nothing, `nodes.fallback` summarizes up to 3
  linked entities' 1-hop neighbourhoods (relationship types, counts, up to 8 example neighbours) plus
  their descriptions. The answer must say the exact relationship wasn't found. If even that is empty,
  the reply is the fixed `prompts.NO_ANSWER` text.
- **Why**: "Not found, but here's what the graph does know" is useful and honest. A fixed message
  for the empty case can't hallucinate.
- **Trade-off**: For made-up entities that weakly match a real one (Floxabutrin → Floxuridine), the
  answer talks about the lookalike. It does say the original wasn't found, but a "did you mean?" step
  would be cleaner.

### D30. Scope check before any retrieval

- **Decision**: `in_scope` from the extraction step routes questions about the asker's own care
  (dosing, diagnosis) or data the graph doesn't hold (prices, statistics) to `out_of_scope`.
- **Why**: Declines early, saving all the downstream LLM calls, and keeps the system from appearing to
  give personal medical advice.
- **Trade-off**: Over-caution. v1 refused "which drugs should be avoided in X" and "how many drugs
  treat X". Fixed by rewriting the scope rules; v2 then refused one valid question because the model
  decided an unusual compound "isn't a real drug". The prompt now says not to judge whether an entity
  exists.
- **Evidence**: Wrong refusals 3.0% (v1) → 0.7% (v2); must-decline questions declined 100% in both.

### D31. Limits on what reaches the answer prompt

- **Decision**: 50-row cap on results, long text cut to 500 characters, serialized rows cut to 12,000
  characters.
- **Why**: Keeps prompts inside the 8K window and the model focused. Long contexts are slower and
  models use the middle of long prompts less reliably.
- **Trade-off**: Lists longer than 50 are partial; the answer says so (`truncated` flag). The
  evaluation scores recall@50 to match.

### D32. Show the evidence with every answer

- **Decision**: The UI shows the pipeline steps, the Cypher, the raw rows and a subgraph for each
  answer.
- **Why**: Grounding is only useful if someone can check it. It also makes debugging fast: you can see
  immediately whether a bad answer came from linking, the query or the answer step.
- **Trade-off**: `build_subgraph` costs two extra Neo4j queries per answer (milliseconds).

---

## 6. Application and tooling

### D33. LangGraph for orchestration

- **Decision**: The pipeline is a LangGraph `StateGraph` with 9 nodes, conditional routing and a
  typed shared state (`backend/app/pipeline/state.py`).
- **Why**: The flow has loops (retry until valid) and branches (decline, fallback, no answer).
  LangGraph makes them explicit: each decision is a small routing function. It streams node updates
  and LLM tokens, which the UI uses. `configurable` lets the evaluation swap models per run.
- **Alternatives**: Plain Python with while-loops (works, but I'd rebuild streaming and tracing),
  a LangChain chain (no loops), a free-form agent (less predictable, harder to evaluate).
- **Trade-off**: Another dependency and some framework concepts (reducers, stream modes) to learn.

### D34. Use LangChain only for model wrappers and structured output

- **Decision**: `ChatOllama`/`ChatOpenAI`, message classes, `with_structured_output`, and the
  `Embeddings` base class. No chains, no ready-made Cypher QA.
- **Why**: Get provider switching and constrained JSON for free, keep full control of the pipeline.
- **Trade-off**: Minor; LangChain's packages change often, so versions are pinned in `uv.lock`.

### D35. FastAPI with server-sent events

- **Decision**: FastAPI app (`backend/app/api/main.py`); `POST /api/chat` returns an SSE stream of
  `step`, `rows`, `token`, `final` and `error` events.
- **Why**: Answers take ~40 s; streaming shows progress at every step and the answer as it's written.
  SSE is one-way and plain HTTP, which is all that's needed. FastAPI validates requests with Pydantic
  (question 2–1,000 characters, ≤ 20 history turns) and generates API docs.
- **Alternatives**: WebSockets (two-way, more complex), polling (laggy, wasteful), Streamlit (fast to
  build, but no control over streaming layout or the evidence panel), Flask (no native async).
- **Trade-off**: The browser's `EventSource` only supports GET, so the frontend reads the POST
  response stream manually in `frontend/src/api.ts`.

### D36. React + TypeScript + Vite frontend with vis-network

- **Decision**: A small React app: chat on the left, evidence panel on the right (Pipeline, Cypher,
  Graph, Rows tabs).
- **Why**: Showing evidence is a core feature (D32), which needs a real UI. vis-network draws the
  subgraph with little code. Vite's proxy makes the API look same-origin in development.
- **Alternatives**: Streamlit or Gradio (quicker, much less control), plain HTML (more manual work).
- **Trade-off**: A Node toolchain in addition to Python.

### D37. Configuration with pydantic-settings, cached singletons

- **Decision**: `backend/app/config.py` `Settings` reads `.env`; `get_settings()`, `get_db()`,
  `get_embeddings()`, `get_entity_embeddings()` and `build_workflow()` are `@lru_cache`d.
- **Why**: Typed config (a typo like `LLM_PROVIDER=olama` fails at startup), secrets out of git, and
  expensive objects (Neo4j driver pool, SapBERT model, compiled graph) created once and reused.
- **Trade-off**: Changing `.env` needs a process restart.

### D38. uv, ruff, pytest

- **Decision**: uv for the Python environment and lockfile (Python 3.12 pinned in `.python-version`),
  ruff for linting, pytest for tests.
- **Why**: uv is fast and gives a reproducible environment from `uv.lock`; the system Anaconda Python
  stays untouched. Tests focus on `guards.py` because it's security code built on regexes, which are
  easy to get subtly wrong.
- **Trade-off**: No tests yet for the LLM-dependent nodes; those are covered by the evaluation instead.

### D39. Makefile for common commands

- **Decision**: `make install | data | import | up | setup | api | ui | test | lint | eval`.
- **Why**: Short, memorable commands and a single place documenting how to run everything.

---

## 7. Evaluation

### D40. Build my own test set with exact gold answers

- **Decision**: `eval/build_golden.py` generates 147 questions; gold answers are the results of
  reference Cypher queries.
- **Why**: Exact gold means exact scoring: no human grading and no LLM judge. Generated from the same
  graph, so every question is answerable (or deliberately not).
- **Alternatives**:
  - *LLM-as-judge*: convenient for free-text, but position, verbosity and self-preference biases, and
    it's another model's opinion.
  - *Existing medical QA benchmarks* (MedQA etc.): test exam knowledge, not faithfulness to a graph.
  - *Neo4j's Text2Cypher dataset*: different schemas.
- **Trade-off**: Measures faithfulness to PrimeKG, not medical truth. Template phrasing is more
  regular than real users.

### D41. Templates + sampled entities, plus hand-written special cases

- **Decision**: 8 list categories × 12 questions from templates with entities sampled (seed 7) among
  those with a reasonable answer size (e.g. 3–30 drugs); plus 8 counts, 10 balanced yes/no, 11 lay
  terms, 10 follow-ups and 12 must-decline questions.
- **Why**: Covers every query pattern and behaviour I cared about, including declining and
  conversation, with a fixed seed for reproducibility. Bounding answer sizes keeps lists checkable.
- **Trade-off**: 147 questions give ±4-point confidence intervals on the main metrics.

### D42. Exclude few-shot entities from the test set

- **Decision**: Every entity quoted in `examples.py` (plus a few extras) is in a `LEAKED` set and
  skipped when sampling.
- **Why**: Otherwise the model could copy an example's answer and the scores would be inflated.

### D43. Metric definitions

- **Decision** (all in `eval/metrics.py`):
  - *Answer recall@50*: gold entities named in the answer ÷ min(|gold|, 50).
  - *Unsupported claims*: named entities not in gold, retrieved rows, or the question.
  - *Off-gold*: named entities not in gold (stricter, counts grounded extras).
  - *Exact match*: the retrieved set equals gold (or is a 50-item subset when gold is larger).
  - Plus first-try validity, value correctness for counts and yes/no, refusal accuracy, false
    refusals, latency p50/p95.
  - Entities are found by matching every node name of the right type in the answer text; no LLM.
- **Why**: Unsupported claims is the precision side, which matters most in medicine (a wrong drug is
  worse than a missing one). Comparing result sets rather than query text accepts any correct query.
  @50 matches the pipeline's row cap. Deterministic matching is cheap and transparent.
- **Trade-off**: Name matching can miss paraphrased names; it can't judge explanatory text.

### D44. Baselines on equal terms

- **Decision**: Closed-book Qwen (147 questions), Llama 3.1 8B in the same pipeline (65 questions, 5
  per category), and nomic vs SapBERT for linking.
- **Why**: A number means nothing without a comparison. Llama ran on a balanced subset to save ~1 hour
  of compute; Qwen was re-scored on the same 65 questions for a fair comparison.
- **Trade-off**: The subset widens Llama's confidence intervals. Latency comparisons were affected by
  machine load (the Llama run happened after a restart cleared swap); accuracy wasn't.

### D45. Fix the measurement when it's wrong, never to get a better number

- **Decision**: Several early "failures" were scoring bugs: overlapping names ("Estradiol" inside
  "Estradiol acetate"), HPO terms that are common words ("Severe"), the question's own subject counted
  as a claim, grounded description text counted as unsupported, gold lists longer than the row cap,
  and "not included" read as a refusal. Each fix is documented at the top of `metrics.py` and applied
  identically to every run.
- **Why**: Trusting a buggy metric leads to fixing things that aren't broken. Reading individual
  failures found every one of these.

### D46. Error analysis between versions

- **Decision**: After v1, I read every failure, grouped them (over-cautious scope, LLM renaming exact
  names, shortened lists, one follow-up miss), fixed the biggest groups with general changes, and
  re-ran all 147 questions.
- **Why**: Re-running everything catches regressions; one appeared (the "not a real drug" refusal) and
  was fixed.
- **Evidence**: v1 → v2: recall 94.0% → 96.8%, exact match 91.5% → 93.2%, wrong refusals 3.0% →
  0.7%, counts/yes-no 94.4% → 100%. The overall exact-match change is within the ±4-point interval, so
  it isn't significant on its own; the targeted fixes (all refused contraindication and count
  questions answered) clearly held.

---

## 8. Operations

### D47. Cap Docker Desktop's VM at 4 GB

- **Decision**: Docker Desktop memory limit set to 4 GB (default was 8 GB on this 16 GB Mac).
- **Why**: Neo4j uses ~2 GB. The extra reservation pushed macOS into heavy swap (~17 GB), which made
  the LLM wait on the SSD: ~80 tokens/s prompt processing instead of thousands, and 100+ s questions.
- **Trade-off**: Less headroom for other containers.

### D48. Stop services when not in use

- **Decision**: Stop the API, UI and Neo4j and unload the model (`ollama stop qwen3.5:9b`) when not
  testing.
- **Why**: Everything shares 16 GB of unified memory with the rest of the laptop.

### D49. Not hosted yet

- **Decision**: Local only for now.
- **Why**: Free GPU hosting is limited (Azure for Students has no GPU quota; free API tiers send data
  off-device). Options if needed: a Hugging Face Docker Space for Neo4j + API + UI with the model on a
  serverless GPU (Modal) or a free API tier.

---

## 9. Summary of trade-offs

| I chose | Over | Gained | Gave up |
|---|---|---|---|
| Knowledge graph | Vector RAG | Exact relations, counts, multi-hop, provenance | Free-text questions outside the schema |
| Local Qwen3.5-9B | API model | Privacy, zero cost, control | Speed (~37 s/question) |
| Text-to-Cypher + validation | Templates | Open-ended questions | Wrong-but-valid queries possible |
| SapBERT + LLM suggestions | General embedder only | 2× linking accuracy, synonyms | Extra model, brand names need the LLM |
| Layered guards | Single check | Robust read-only guarantee, useful retry feedback | Maintenance, a little latency |
| Fallback + fixed no-answer | Always answer | Honest abstention | Some answers about lookalike entities |
| Own exact-gold eval | LLM judge | Deterministic, unbiased scores | Faithfulness to PrimeKG, not medical truth |
| Temperature 0 | Sampling | Repeatable outputs and fair comparisons | No variety in retries without new feedback |

## 10. Decisions I'd revisit

- **Add a claim checker** after the answer step that verifies every named entity against the rows
  and removes or flags anything unsupported (grounding enforced, not just requested).
- **Pass previous answers' entities directly** into follow-up queries, instead of relying only on
  rewriting, to fix multi-item follow-ups.
- **Ask "did you mean?"** when linking is only approximate, instead of answering about a lookalike.
- **Fine-tune a small Text-to-Cypher model** on Neo4j's dataset plus this schema for speed.
- **Add a vector index over descriptions** for explanatory questions (hybrid RAG).
- **Write natural, non-template test questions** and grow the set to ~1,000 to detect small changes.
- **Merge or skip the entity-extraction call** when the question has an exact entity match, to cut
  ~20 s per question.
