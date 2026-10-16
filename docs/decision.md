# Design decisions, explained from scratch

This document explains every important choice in MediGraph. It's written for someone new to machine
learning, so each decision starts by explaining **what the thing is** and **what its main job is**
before getting into **why I chose it**.

Every decision follows the same structure:

1. **What is it?** A from-scratch explanation, with an everyday comparison where it helps.
2. **How does it work?** The mechanism, kept simple, usually with a small example.
3. **What it does in MediGraph.** Its exact role and where it lives in the code.
4. **The decision and the reasoning.** What I chose and why, step by step.
5. **Alternatives.** What each alternative is, and why I didn't pick it.
6. **Trade-offs.** What this choice costs.
7. **Evidence.** Measurements, where I have them.
8. **In an interview.** A short spoken answer.

Decisions are numbered (D1, D2, ...) so other docs can refer to them. If a term is unfamiliar, check
the glossary first; most terms are also explained in full where they first matter.

---

## Contents

- [Glossary: the basic words](#glossary-the-basic-words)
- [The project in one picture](#the-project-in-one-picture)
- [Part 1: Problem framing](#part-1-problem-framing) (D1–D3)
- [Part 2: Data](#part-2-data) (D4–D7)
- [Part 3: Database](#part-3-database) (D8–D11)
- [Part 4: Models](#part-4-models) (D12–D19)
- [Part 5: Pipeline design](#part-5-pipeline-design) (D20–D32)
- [Part 6: Application and tooling](#part-6-application-and-tooling) (D33–D39)
- [Part 7: Evaluation](#part-7-evaluation) (D40–D46)
- [Part 8: Operations](#part-8-operations) (D47–D49)
- [Summary of trade-offs](#summary-of-trade-offs)
- [Decisions I'd revisit](#decisions-id-revisit)

---

## Glossary: the basic words

| Term | Plain meaning |
|---|---|
| **Model** | A mathematical function with millions or billions of adjustable numbers ("weights") that has learned a pattern from examples. |
| **LLM (large language model)** | A model trained on huge amounts of text to predict the next word. Chatbots like ChatGPT, and Qwen in this project, are LLMs. |
| **Token** | The unit of text an LLM reads and writes: a word or a piece of a word. "Allopurinol" might be 3–4 tokens. |
| **Prompt** | The text you send to an LLM: instructions, data and the question. |
| **Inference** | Using a trained model to produce output (as opposed to training it). Everything MediGraph does with models is inference. |
| **Hallucination** | When an LLM states something false or unsupported, fluently and confidently. |
| **Embedding** | A list of numbers (a vector) that represents the meaning of a piece of text, so similar meanings get similar numbers. |
| **Vector search** | Finding the stored embeddings closest to a query embedding, i.e. finding text with similar meaning. |
| **RAG (retrieval-augmented generation)** | First fetch relevant facts from a trusted source, then have the LLM answer using only those facts. |
| **Knowledge graph** | Facts stored as things (nodes) connected by named relationships (edges): `Allopurinol —INDICATED_FOR→ gout`. |
| **Neo4j** | A database designed to store and search graphs. |
| **Cypher** | Neo4j's query language: `MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name:'gout'}) RETURN d.name`. |
| **Text-to-Cypher** | Having an LLM translate a plain-English question into a Cypher query. |
| **Entity linking** | Matching words in a question ("heart attack") to the exact item in the database ("myocardial infarction"). |
| **Quantization** | Storing a model's numbers with fewer bits so it uses less memory. |
| **API** | A defined way for one program to call another, e.g. the browser calling the Python server over HTTP. |
| **Evaluation** | Measuring how well a system works on test questions with known answers. |
| **Baseline** | A simpler system you compare against, so a number means something. |

---

## The project in one picture

```
 You type:  "What drugs treat a heart attack?"
                 │
                 ▼
 ┌───────────────────────────────────────────────────────────────────────┐
 │ 1. Understand the question   (LLM: find "heart attack", suggest       │
 │                               "myocardial infarction", check scope)   │
 │ 2. Link to the graph         (find the exact node in Neo4j)           │
 │ 3. Write a database query    (LLM writes Cypher)                      │
 │ 4. Check the query           (read-only? valid? names exist?)         │
 │ 5. Run it on Neo4j           (returns 9 drugs)                        │
 │ 6. Write the answer          (LLM, using ONLY those 9 rows)           │
 └───────────────────────────────────────────────────────────────────────┘
                 │
                 ▼
 Answer + the query + the raw rows + a picture of the subgraph
```

The whole design rests on one idea: **the language model never answers from its own memory. It
only reads and writes; the facts come from the database.** Most decisions below either support that
idea or deal with what it costs.

---

# Part 1: Problem framing

These decisions define what the project is. Everything else follows from them.

---

## D1. Answer from retrieved facts, never from the model's memory

### What is it?

There are two ways to get an answer out of an LLM:

- **Closed-book:** ask the model directly. It answers from whatever it absorbed during training.
- **Grounded (open-book):** first look the facts up in a trusted source, put them in the prompt, and
  tell the model to answer only from them.

Think of a student in an exam. Closed-book, they write from memory and, if they don't know, they may
guess with confidence. Open-book, they find the right page and write from it, and if it isn't in the
book they can say so. The primary job of grounding is to make every claim come from a source you
can point to.

### How does it work?

An LLM is trained to predict the next word of text (see D13). After reading a large share of the
internet, it can produce fluent text on almost any topic. But:

- Its knowledge is **frozen** at the date training stopped (the "knowledge cutoff").
- Its knowledge is **frequency-weighted**: it knows common facts (aspirin) far better than rare ones
  (the genes of a rare syndrome).
- It has **no sources**: it can't tell you where a fact came from, and it can't reliably tell when
  it doesn't know.
- It's trained to produce **likely** text, not **true** text. A made-up but plausible-sounding drug
  list is "likely text".

Grounding changes the model's job from *remembering* facts to *reading* them, which LLMs do much
more reliably.

### What it does in MediGraph

The answer step (`answer()` in `backend/app/pipeline/nodes.py`) only receives the rows returned by
the database query, with an instruction in `prompts.ANSWER`: *"Use only the facts in Retrieved data.
Do not add drugs, genes, symptoms or claims that are not there, even if you believe them to be true."*
If nothing is found, a fixed message is returned instead of anything model-written (D29).

### The decision and the reasoning

**Decision:** every answer is built only from database results. The model is never asked a medical
question directly.

**Reasoning:**

1. **The cost of a wrong answer is high in medicine.** A fluent wrong answer ("drug X is safe in
   condition Y") is worse than "I don't know", because people trust fluent text.
2. **Rare facts are where models fail most.** PrimeKG has 17,080 diseases; most are rare. A model has
   seen little or nothing about most of them, so this is exactly where closed-book answers break.
3. **Traceability.** With grounding, every answer can show the exact query and rows behind it. A
   reader (or interviewer) can verify any claim.
4. **Abstention becomes possible.** If the database returns nothing, the system *knows* it has
   nothing, and can say so. A closed-book model has no such signal.
5. **It makes the problem measurable.** With a fixed source, "is this answer supported?" becomes a
   question you can check automatically (D43).

### Alternatives

- **Ask the model directly (closed-book).** Simplest possible system. Rejected because it's the very
  problem I wanted to solve. I kept it as a baseline (D44) to measure how much grounding helps.
- **Fine-tune a medical model.** Fine-tuning means training an existing model further on medical text
  so it "knows more". It still answers from memory, still can't cite sources, is expensive to redo
  when facts change, and still hallucinates on rare facts.
- **Use a bigger, smarter model.** Larger models hallucinate less, but not zero, and still have a
  knowledge cutoff and no sources.

### Trade-offs

- **Coverage:** the system can only answer what the graph contains. "What drugs treat cancer?"
  returns the three drugs PrimeKG lists under that exact disease node, not what an oncologist would say.
- **More moving parts:** a database, a query step, checks and retries, instead of one model call.
- **Grounded is not the same as true:** if the source is wrong or out of date, the answer faithfully
  repeats it.

### Evidence

Same model (Qwen3.5-9B), same 147 questions:

| | Closed-book | Grounded (v2) |
|---|---|---|
| Correct items found (answer recall) | 19.8% | 96.8% |
| Named items with nothing behind them (unsupported claims) | 39.1% | 0.0% |
| Correctly declined unanswerable questions | 25% | 100% |

### In an interview

"The model never answers from memory. Every answer is generated from rows a database query returned,
and if the query finds nothing, the system says so with a fixed message. Asked directly, the same
model made unsupported claims 39% of the time; grounded, zero on 147 test questions."

---

## D2. Use a knowledge graph instead of a vector store over documents

### What is it?

There are two main kinds of "trusted source" for grounding:

- **A vector store over documents** (the most common RAG setup). You take documents (drug labels,
  papers), cut them into short passages ("chunks"), turn each chunk into an embedding (a vector that
  represents its meaning, see D18), and store them. To answer a question, you embed the question and
  fetch the chunks whose embeddings are closest, i.e. the passages that *look* most similar to the
  question.
- **A knowledge graph.** Facts are stored as **nodes** (things: drugs, diseases, genes) connected by
  **edges** (named relationships). Each edge is one fact:

```
(Allopurinol) ──INDICATED_FOR──▶ (gout)
(Allopurinol) ──HAS_SIDE_EFFECT──▶ (Rash)
(gout) ──HAS_PHENOTYPE──▶ (Joint swelling)
```

You answer questions by **querying** the graph: "start at gout, follow INDICATED_FOR edges backwards,
return the drugs".

A library comparison: a vector store is like searching a library for pages that read similarly to
your question. A knowledge graph is like a well-organized index card system where every card states
exactly one fact and links to related cards.

### How does it work?

Take the question "Which drugs target genes associated with Alzheimer disease?"

- **Vector store:** embed the question, fetch the 5–10 most similar chunks. You might get passages
  about Alzheimer genetics and passages about some drugs, but the specific combination (genes linked
  to the disease AND drugs that act on those genes) is rarely written in one passage. The LLM must
  piece it together from whatever chunks came back, and can't know what it missed.
- **Knowledge graph:** follow two hops: Alzheimer disease → its associated genes → drugs that target
  those genes. The database returns the complete, exact list.

```cypher
MATCH (:Disease {name: 'Alzheimer disease'})-[:ASSOCIATED_WITH_GENE]->(g:Gene)<-[:TARGETS]-(d:Drug)
RETURN g.name, collect(d.name)
```

### What it does in MediGraph

PrimeKG is loaded into Neo4j (D4–D8). The LLM writes Cypher queries against it (D20). There are no
document chunks anywhere in the project.

### The decision and the reasoning

**Decision:** retrieve facts from a knowledge graph.

**Reasoning:**

1. **My questions are about relationships.** "Which drugs treat X", "which genes are linked to Y",
   "which of *those* also cause Z". A graph stores relationships directly; documents store them
   implicitly in sentences.
2. **Multi-hop questions become simple.** Each extra step is one more arrow in the query, not a hope
   that some passage happens to connect the dots.
3. **Counting and filtering are exact.** "How many drugs are indicated for hypertension?" is
   `count(...)` in a query and returns 103. With documents, the model would have to find and count
   103 mentions across many chunks, most of which wouldn't fit in the prompt.
4. **Follow-ups work naturally.** "Which of those cause nausea?" becomes a query over the exact drug
   names from the previous answer.
5. **No chunking problems.** With documents you must decide how to cut text, and a fact can be split
   across chunks or lose its subject ("It may cause rash" — which drug?). In a graph each fact is one
   edge with its subject and object explicit.
6. **Every answer has precise provenance:** the query and the rows.

### Alternatives

- **Vector RAG over documents.** Described above. Better for "explain how allopurinol works" or
  "summarize the guideline". Rejected as the main approach because it's weak at counting, filtering,
  intersections and multi-hop questions, which were the questions I cared about.
- **Hybrid (graph + vector index over descriptions).** Combines both: the graph for relationships, a
  vector index over text descriptions for explanatory questions. More capable but more moving parts.
  It's on my improvement list (see the end of this document).
- **A relational database (SQL tables).** Can store the same facts, but every hop becomes a JOIN
  between tables, queries get long, and LLMs make more mistakes writing them (see D8).

### Trade-offs

- Questions the schema doesn't cover (dosing, detailed mechanisms, clinical guidelines) can't be
  answered from structure alone.
- The graph only knows what was put into it. Building or choosing a good graph is real work (D4–D6).

### Evidence

On count and yes/no questions, the graph pipeline got 100% right (v2), versus 50% for the same model
answering from memory.

### In an interview

"My questions are relational: which drugs, which genes, which of those, how many. A graph stores
those relationships explicitly, so multi-hop questions are traversals and counts are exact. Vector
search finds text that looks similar, which is weak for 'which of those' and counting."

---

## D3. Run everything locally

### What is it?

"Local-first" means every part of the system runs on your own computer: the language model, the
database, the server and the user interface. The alternative is calling **hosted services** over the
internet, such as the OpenAI API (you send your prompt to their servers and pay per token).

### How does it work?

- The LLM runs through **Ollama** (D12), which loads the model into the Mac's memory and GPU and
  answers requests on `localhost:11434`.
- Neo4j runs in **Docker** (D9) on `localhost:7687`.
- The Python API runs on `localhost:8000` and the web UI on `localhost:5173`.

Nothing is sent to any outside service when you ask a question.

### What it does in MediGraph

Everything in the request path is local. The code can switch to OpenAI with one setting (D17), but
the default and everything in the evaluation is local.

### The decision and the reasoning

**Decision:** run the full system on my laptop.

**Reasoning:**

1. **Privacy.** Medical questions can reveal personal health information. Local means no data leaves
   the machine. That's a real property, not just a talking point.
2. **Cost.** Each full evaluation run is ~150 questions × 3–4 LLM calls. Running locally is free, so I
   could re-run the evaluation after every change (I ran it four times, plus partial runs).
3. **Learning.** Making a 9B-parameter model work well forced me to understand the pipeline: entity
   linking, validation, retries. With a frontier API model, a lot of that is hidden by the model just
   being smarter.
4. **Control and reproducibility.** The exact model version is pinned on disk and can't change under
   me, which matters for comparing evaluation runs.

### Alternatives

- **Hosted API model** (OpenAI, Anthropic, Google). Much faster and stronger. Costs money per call,
  sends data to a third party, and the model can change over time.
- **Rented cloud GPU** running the same open model. Faster than a laptop, keeps the open-model story,
  but costs money. Student cloud credits (Azure for Students) don't include GPUs.

### Trade-offs

- **Speed:** about 37 seconds per question (median), almost all of it spent in the LLM. The Neo4j
  queries take tens of milliseconds.
- **Hardware limits:** 16 GB of memory caps the model size at ~9B parameters (D13) and caused memory
  problems I had to manage (D47).

### Evidence

Per-step timing for one question (67.8 s total on a busy laptop): entity extraction 35.0 s, writing
Cypher 16.9 s, validation 0.3 s, **running the query 0.03 s**, writing the answer ~15 s.

### In an interview

"Medical questions can be sensitive, so I kept everything local: no data leaves the machine, and the
evaluation is free to re-run. The cost is speed, about 37 seconds per question on a laptop. The model
is behind one config setting, so switching to a hosted model is a configuration change, not a rewrite."

---

# Part 2: Data

A grounded system is only as good as its source. These decisions are about which knowledge to use
and how to shape it so a language model can query it reliably.

---

## D4. Use PrimeKG as the knowledge source

### What is it?

**PrimeKG** (Precision Medicine Knowledge Graph) is a public biomedical knowledge graph published by
researchers at Harvard (Chandak, Huang & Zitnik, *Scientific Data*, 2023). It combines about 20
existing medical databases into one graph centred on diseases.

To understand why that matters, you need to know that medical knowledge is spread across many
specialised databases, each with its own IDs and naming:

| Source (examples) | What it contributes |
|---|---|
| DrugBank | Drugs, what proteins they act on, approvals |
| SIDER | Drug side effects |
| DisGeNET | Which genes are associated with which diseases |
| MONDO | A standard list of diseases with a hierarchy |
| HPO (Human Phenotype Ontology) | Symptoms and clinical signs |
| Gene Ontology, Reactome | Biological processes and pathways |
| UBERON, Bgee | Anatomy and where genes are expressed |
| Mayo Clinic, Orphanet | Text descriptions of diseases and symptoms |

Joining these yourself means matching IDs across sources, resolving duplicates and cleaning names,
which is months of expert work. PrimeKG has already done it.

### How does it work?

PrimeKG is distributed as files: a list of nodes (129,375 entities, each with a type, a name and a
source ID) and a list of edges (each saying "node A has relationship R with node B"). It also ships
text files with drug descriptions and disease descriptions. You download them and load them into a
graph database.

### What it does in MediGraph

It's the only source of facts. `data/scripts/download_primekg.sh` downloads it from Harvard
Dataverse; `data/scripts/build_import.py` converts it for Neo4j (D6).

### The decision and the reasoning

**Decision:** use PrimeKG.

**Reasoning:**

1. **It has the relationships my questions need:** indications (drug treats disease),
   contraindications, side effects, drug targets and metabolising enzymes, disease symptoms, disease
   genes, environmental exposures.
2. **It's curated, not generated.** Every fact comes from an established database. That matters
   because the whole point of the project is grounding in trustworthy facts; a graph built by an LLM
   could contain hallucinations.
3. **It's disease-centred,** with 17,080 diseases, many of them rare. Rare diseases are exactly
   where LLMs are weakest (D1), so this is where grounding helps most.
4. **It includes text.** Drug mechanisms and disease symptom summaries let the system answer "what
   is X" and "how does X work", not only relationship questions.
5. **It's open (CC BY 4.0) and easy to get:** one download, no license application.
6. **It's published and peer-reviewed,** so I could check my import against the paper's numbers.

### Alternatives

- **Hetionet** (2017): an earlier biomedical graph used widely in research. Smaller and with fewer
  clinical relationships (side effects, contraindications) than PrimeKG.
- **SPOKE:** a much larger biomedical graph, but access and setup are more involved.
- **UMLS / SNOMED CT:** huge medical vocabularies with synonyms and hierarchies. They're dictionaries
  of concepts more than graphs of clinical facts, and they require a license.
- **Build my own graph from papers using an LLM:** flexible, but extraction mistakes would end up in
  the data, defeating the purpose of grounding.
- **Wikidata:** very large and free, but its medical coverage is uneven and its schema is complex.

### Trade-offs

- **It's a snapshot.** Drugs approved or indications added after it was built are missing.
- **Some quirks.** Some diseases were grouped by the authors, so near-duplicates exist
  ("hypertension" and "hypertensive disorder" both have 103 drugs). Some answers look odd from a
  clinical view ("cancer" lists only three drugs) because the data is attached to more specific
  disease nodes.
- **Imbalanced.** Most edges are gene expression (1.5M) and drug–drug interactions (1.3M); the
  clinical edges are a small fraction.

### In an interview

"I used PrimeKG, a peer-reviewed knowledge graph that merges about 20 curated medical databases. It
has the relationships I needed, covers 17,000 diseases including rare ones, and every fact comes from
a curated source rather than being generated, which matters when the whole point is grounding."

---

## D5. Load the full graph, not a subset

### What is it?

A **subset** would mean loading only some node and edge types (say drugs, diseases, symptoms and
genes, plus the clinical relationships between them) and leaving the rest out (gene expression in
tissues, protein–protein interactions, pathways, biological processes).

### What it does in MediGraph

All 129,375 nodes and 4,050,249 relationships are in Neo4j.

### The decision and the reasoning

**Decision:** load everything.

**Reasoning:**

1. **Multi-hop questions can need any part.** "Which drugs target genes associated with disease X"
   passes through genes. "Which pathways is BRCA1 involved in" needs pathways. Cutting parts out
   silently limits what can be asked.
2. **It's cheap at this size.** Neo4j handles 4 million edges easily, the bulk import takes seconds
   (D7), and the database uses about 2 GB of memory.
3. **It's the honest version of the data.** If the full graph says something, the system says it.

### Alternatives

- **A clinical subset** (~130K nodes, ~370K relationships). Smaller and faster to load, and small
  enough for Neo4j's free cloud tier (AuraDB Free allows roughly 200K nodes and 400K relationships).
  Kept in mind for cloud hosting (D49).

### Trade-offs

- More memory and disk. Hub nodes (very common genes and symptoms) have thousands of edges, so
  careless queries over them could be slow; the row cap and timeout (D31, D26) protect against that.

---

## D6. Reshape PrimeKG before loading it

### What is it?

Raw PrimeKG is not ideal for an LLM to query. Before loading, `build_import.py` changes how the data
is labelled and stored, without changing the facts themselves. Four changes:

1. **Clean labels.** PrimeKG's node types are like `gene/protein` and `effect/phenotype`. These become
   `Gene` and `Phenotype`, which are valid, readable Neo4j labels.
2. **Readable relationship names.** PrimeKG uses generic names like `drug_protein`, `disease_protein`,
   `drug_effect`. These become verbs that say what the fact means: `TARGETS`, `ASSOCIATED_WITH_GENE`,
   `HAS_SIDE_EFFECT`. `drug_protein` is split into four kinds using PrimeKG's `display_relation`
   column: `TARGETS`, `METABOLIZED_BY` (enzymes), `TRANSPORTED_BY`, `CARRIED_BY`.
3. **One direction per edge.** PrimeKG stores every edge twice, once each way. The script gives every
   label a rank (Drug 0, Disease 1, Exposure 2, Phenotype 3, ..., Gene 9), turns each edge to point
   from the lower rank to the higher, and deletes the duplicate copy.
4. **Text properties.** Drug `description` and `mechanism`, and disease `description` and `symptoms`,
   are cleaned and attached to the nodes (cut to 700 characters).

### How does it work?

The direction step on one example:

```
raw row 1:  x = gout (Disease, rank 1)      y = Allopurinol (Drug, rank 0)   relation: indication
raw row 2:  x = Allopurinol (Drug, rank 0)  y = gout (Disease, rank 1)       relation: indication

row 1: rank(x) > rank(y) → swap      → (Allopurinol)-[:INDICATED_FOR]->(gout)
row 2: rank(x) < rank(y) → keep      → (Allopurinol)-[:INDICATED_FOR]->(gout)
drop duplicates                      → one edge
```

For same-type edges (drug–drug, gene–gene, disease hierarchy), the edge points from the smaller ID to
the larger, so the duplicate is still detected.

### What it does in MediGraph

The LLM sees a schema that reads almost like English:

```
(:Drug)-[:INDICATED_FOR]->(:Disease)        // drug is an approved treatment for the disease
(:Drug)-[:HAS_SIDE_EFFECT]->(:Phenotype)    // drug can cause this side effect
(:Disease)-[:ASSOCIATED_WITH_GENE]->(:Gene) // gene is associated with the disease
```

### The decision and the reasoning

**Decision:** reshape the data during conversion.

**Reasoning:**

1. **LLMs write better queries against readable schemas.** The model has to guess which relationship
   answers "what treats gout?". `INDICATED_FOR` is much easier to pick than `indication` vs
   `drug_protein` vs `off-label use`. A clean schema is part of prompt engineering.
2. **Consistent direction makes direction checkable.** Because every relationship type always points
   the same way (Drug → Disease), the validator can tell the model "this edge goes the other way"
   (D26). With duplicated both-way edges, there'd be nothing to check.
3. **Duplicates double-count.** With both copies, `count()` queries and undirected matches return
   every fact twice.
4. **Text makes "what is X" answerable** directly from the graph.
5. **It's a correctness check.** After removing duplicates, the edge count should match what the
   PrimeKG paper reports.

### Alternatives

- **Load raw PrimeKG as is.** Less code, but the model would face ambiguous names, both-way edges and
  duplicate counts, and direction couldn't be validated.
- **Rename in the prompt only** (tell the model "drug_protein means target"). Keeps raw data but makes
  the prompt longer and the model's job harder.

### Trade-offs

- **Some relationships are truly two-way** (drug–drug interactions, protein–protein interactions,
  disease hierarchy). They're stored one way and marked "undirected" in the schema, so queries must
  match them without an arrow (`-[:SYNERGISTIC_WITH]-`). The validator accepts either direction for
  those.
- **More conversion code to maintain.** An unmapped relation type makes the script stop with an error
  rather than silently dropping data.

### Evidence

After conversion: 8,100,498 raw rows → **4,050,249** edges, exactly the number in the PrimeKG paper.

### In an interview

"PrimeKG stores every edge twice with generic names. I gave each relationship a readable verb and a
single direction and removed the duplicates. That made the schema easier for the model to query,
made direction errors detectable, and the final count matched the paper exactly, which validated the
conversion."

---

## D7. Bulk-import with `neo4j-admin`

### What is it?

There are several ways to get data into Neo4j:

- **`neo4j-admin database import`:** an offline tool that reads CSV files and writes Neo4j's storage
  files directly, without going through the database's normal query engine. It only works into an
  empty database.
- **`LOAD CSV`:** a Cypher command that reads a CSV inside a normal transaction.
- **Driver inserts:** a Python program sends `CREATE` or `MERGE` queries, ideally in batches.

### How does it work?

The CSV headers tell the tool what each column means:

```
nodes.csv:          node_index:ID, name, source_id, source, description, mechanism, symptoms, :LABEL
relationships.csv:  :START_ID, :END_ID, :TYPE
```

`make import` runs the tool inside the Neo4j Docker image, pointed at those two files.

### The decision and the reasoning

**Decision:** use `neo4j-admin database import`.

**Reasoning:**

1. **Speed.** It loaded 4 million relationships in about 3 seconds. `LOAD CSV` or batched inserts
   would take minutes to hours.
2. **Simplicity.** One command, no import code to write or debug.
3. **The data doesn't change.** PrimeKG is a static snapshot, so "load once into an empty database"
   is exactly the use case.

### Alternatives

- **`LOAD CSV`:** fine for medium imports into a running database, much slower here.
- **`MERGE` from Python:** flexible (handles updates), but very slow for millions of rows.

### Trade-offs

- **It overwrites the database.** Any change to the data means re-running the whole conversion and
  import. Incremental updates would need a different approach.

---

# Part 3: Database

The graph needs a database that can store it, search it quickly, find entities by name or meaning,
and guarantee that the LLM can't change it.

---

## D8. Use Neo4j as the graph database

### What is it?

A **database** stores data and lets you query it. Most databases you'll meet are **relational**
(PostgreSQL, MySQL): data lives in tables of rows and columns, and you connect tables with JOINs in
SQL. A **graph database** stores nodes and relationships directly, and is built to follow
relationships quickly.

**Neo4j** is the most widely used graph database. Its main ideas:

- **Nodes** have **labels** (`:Drug`, `:Disease`) and **properties** (`name: "Metformin"`).
- **Relationships** have a **type** (`:INDICATED_FOR`), a direction, and optionally properties.
- You query with **Cypher**, a language where you draw the pattern you want with ASCII art:
  `(d:Drug)-[:INDICATED_FOR]->(x:Disease)`.

### How does it work?

The key internal idea is **index-free adjacency**: each node stores direct pointers to its
relationships, and each relationship points to the nodes at its ends. Following an edge is just
following a pointer, so the cost of a query depends on how much of the graph it touches, not on how
big the database is. In a relational database, each hop is a JOIN that looks up an index, which gets
more expensive as tables grow and as hops multiply.

Same question in both, "drugs that target genes associated with Alzheimer disease":

```sql
-- SQL: four JOINs
SELECT DISTINCT dr.name FROM diseases ds
JOIN disease_gene dg ON dg.disease_id = ds.id
JOIN genes g        ON g.id = dg.gene_id
JOIN drug_target dt ON dt.gene_id = g.id
JOIN drugs dr       ON dr.id = dt.drug_id
WHERE ds.name = 'Alzheimer disease';
```

```cypher
// Cypher: one pattern
MATCH (:Disease {name:'Alzheimer disease'})-[:ASSOCIATED_WITH_GENE]->(:Gene)<-[:TARGETS]-(d:Drug)
RETURN DISTINCT d.name
```

### What it does in MediGraph

It stores PrimeKG and answers every query: the generated Cypher, the entity-linking searches (exact,
full-text and vector), the validation `EXPLAIN`s and the subgraph for the UI. The client code is
`backend/app/graph/db.py`.

### The decision and the reasoning

**Decision:** Neo4j 5.26 Community edition.

**Reasoning:**

1. **The data is a graph** and the questions are graph questions (D2), so a graph database is the
   natural fit.
2. **Cypher is easy for LLMs.** It's readable, visually matches the question, and there's a lot of
   Cypher in public code and docs that models have seen during training. Fewer places to make a
   mistake than a long SQL JOIN chain.
3. **Built-in search indexes.** Neo4j has full-text (Lucene) and vector (HNSW) indexes, so entity
   linking needs no second database (D10, D11).
4. **`EXPLAIN` reports the query type** (read, write, read-write, schema). That became one of the key
   safety checks (D26).
5. **Easy to run locally** in Docker, with a browser UI at `localhost:7474` for exploring the data.
6. **Free Community edition** covers everything this project needs.

### Alternatives

- **PostgreSQL (+ pgvector):** the most popular relational database. Can store the same data in tables,
  but multi-hop questions become chains of JOINs, which are longer for an LLM to write and slower as
  hops increase.
- **Memgraph:** a Cypher-compatible graph database focused on speed. Smaller ecosystem and less
  tooling than Neo4j.
- **Amazon Neptune:** a managed graph database on AWS. Cloud-only, not local.
- **An RDF triple store with SPARQL** (e.g. GraphDB, Blazegraph): stores facts as subject–predicate–
  object triples identified by URIs. Strong for standards and linked public data, but SPARQL is more
  verbose and harder for small models to write.

### Trade-offs

- **Community edition has no role-based users.** I can't create a database user that is physically
  limited to reading. Instead, every generated query runs in a READ access-mode session, plus static
  checks and `EXPLAIN` (D26). Enterprise edition would add proper read-only roles.
- **Another service to run** (Docker, memory) alongside the model.

### In an interview

"The data is a graph and the questions are graph traversals, so I used Neo4j. Cypher is readable for
both people and LLMs, it has built-in full-text and vector indexes so I didn't need a separate search
system, and its EXPLAIN reports whether a query writes, which I use as a safety check."

---

## D9. Run Neo4j in Docker with fixed memory settings

### What is it?

**Docker** runs software in **containers**: isolated, packaged environments that include everything
the software needs. Instead of installing Neo4j (and Java) on your Mac, you run Neo4j's official
**image** (a ready-made package) as a container.

- **`docker-compose.yml`** describes the setup in one file: which image, which ports, environment
  settings, and where to store data.
- A **volume** is storage that survives when the container is stopped or deleted.
- A **healthcheck** is a command Docker runs to know when the service is ready.

### How does it work?

```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    ports: ["7474:7474", "7687:7687"]        # browser UI, and the Bolt protocol for the Python driver
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-***REMOVED***}
      NEO4J_server_memory_heap_max__size: 1G   # memory for query processing
      NEO4J_server_memory_pagecache_size: 1G   # memory for caching the data files
      NEO4J_db_transaction_timeout: 15s        # kill any query running longer than this
    volumes:
      - neo4j_data:/data                       # the graph survives restarts
      - ./data/import:/import:ro               # CSVs for the bulk import, read-only
```

`make up` runs `docker compose up -d --wait`, which starts the container and waits until the
healthcheck passes.

### The decision and the reasoning

**Decision:** Neo4j in Docker with 1 GB heap, 1 GB page cache and a 15 s transaction timeout.

**Reasoning:**

1. **Reproducible:** anyone can start the same database with one command, on any OS.
2. **Clean:** no Java or Neo4j installed on the Mac; delete the container and it's gone.
3. **Data persists** in the volume, so stopping Docker doesn't lose the 4M-edge graph.
4. **Memory sized for a shared laptop.** 1 GB + 1 GB is enough for this graph while leaving room for
   the 6 GB model. The default settings would grab more.
5. **The 15 s server-side timeout** is a backstop if a query somehow runs away, independent of the
   10 s timeout set in the Python client.

### Alternatives

- **Install Neo4j Desktop natively:** easier GUI, but less reproducible and harder to describe in a README.
- **Neo4j AuraDB (cloud):** no local resources, but the free tier is too small for the full graph and
  data leaves the machine.

### Trade-offs

- Docker Desktop on a Mac runs a Linux virtual machine that reserves memory up front. That reservation
  turned out to matter a lot (D47).

---

## D10. Indexes: one per label for names and IDs, one full-text, one vector per label

### What is it?

An **index** is a side structure that lets a database find things without checking every row, like
the index at the back of a book. Neo4j has several kinds:

| Index type | What it's for | Example |
|---|---|---|
| **Range** | Exact matches and ranges on a property | Find the Disease whose `name` is exactly "gout" |
| **Full-text** (Lucene) | Word search, typos, relevance scoring | Find nodes whose name contains something like "diabetis" |
| **Vector** (HNSW) | Nearest neighbours of an embedding | Find names whose meaning is closest to "heart attack" |

### How does it work?

Without an index, `MATCH (n:Disease {name: 'gout'})` checks all 17,080 Disease nodes. With a range
index on `Disease.name`, it jumps straight to the right one.

Indexes in Neo4j belong to a **label**. That's why `MATCH (n {name: 'gout'})` (no label) can't use
them and scans all 129,375 nodes, while `MATCH (n:Disease {name: 'gout'})` is instant. MediGraph's
code always includes a label for this reason.

### What it does in MediGraph

Created by `create_indexes()` and `embed_names()` in `backend/app/graph/setup.py`:

- `<label>_name` and `<label>_idx` range indexes for all 10 labels (e.g. `disease_name`, `drug_idx`).
- One full-text index, `entity_names`, across the 7 labels people ask about.
- One vector index per embedded label: `sapbert_disease`, `sapbert_drug`, and so on.

### The decision and the reasoning

**Decision:** create all three kinds.

**Reasoning:**

1. **Exact lookups must be instant.** Entity linking, name validation and query execution all start
   from a name. Range indexes make those lookups milliseconds.
2. **Users misspell.** "diabetis" has no exact match; full-text search with fuzzy matching finds
   "diabetes".
3. **Users use different words.** "heart attack" shares no words with "myocardial infarction"; only
   vector search (with a good embedding model, D18) connects them.
4. **`node_index` indexes** make the fallback's and subgraph's lookups by ID fast.

### Trade-offs

- Indexes use disk and slow down writes. The data never changes after import, so this costs nothing
  in practice.

---

## D11. Store embeddings inside Neo4j, not in a separate vector database

### What is it?

A **vector database** (Pinecone, Qdrant, Weaviate, Chroma, pgvector) is a database specialised in
storing embeddings and finding the nearest ones quickly, usually with an **HNSW** index (a layered
graph of vectors you can hop through to reach the nearest neighbours without comparing against all of
them). Most RAG tutorials use one.

Neo4j 5 can do the same thing: store a vector as a node property and build an HNSW vector index on it.

### How does it work?

`setup.py` embeds each node's name with SapBERT (768 numbers), stores it as the node's `sapbert`
property with `db.create.setNodeVectorProperty`, then creates a vector index. At query time:

```cypher
CALL db.index.vector.queryNodes('sapbert_disease', 3, $vector) YIELD node, score
RETURN node.name, score
```

returns the 3 diseases whose name embeddings are closest to `$vector`.

### What it does in MediGraph

Entity linking (`vector_candidates()` in `backend/app/graph/linker.py`) and the linking benchmark use
these indexes.

### The decision and the reasoning

**Decision:** keep vectors on the nodes in Neo4j.

**Reasoning:**

1. **The things I search for are graph nodes.** A vector hit is directly a node with its
   `node_index`, ready to use in a Cypher query. With a separate vector database, I'd have to store
   node IDs there and map results back.
2. **One system instead of two:** no second service to run, and nothing to keep in sync if the graph
   changes.
3. **The scale is small:** about 85,000 vectors. Neo4j's built-in HNSW index handles that easily.

### Alternatives

- **Qdrant / Weaviate / Milvus:** dedicated open-source vector databases with more tuning options and
  better scaling to hundreds of millions of vectors.
- **Chroma:** a lightweight embedded vector store, popular for prototypes.
- **FAISS:** a library (not a database) for fast vector search, from Meta.
- **pgvector:** vectors inside PostgreSQL.

All would work; all add a second store for no benefit at this size.

### Trade-offs

- Fewer knobs than a dedicated vector database, and less suited to very large collections. Neither
  matters at 85,000 vectors.

### In an interview

"The entities I search for are graph nodes, so I stored their embeddings on the nodes and used
Neo4j's built-in vector index. A hit is directly a node I can put in a Cypher query, with no second
database to keep in sync. At 85,000 vectors, a dedicated vector database would add complexity
without any benefit."

---

# Part 4: Models

MediGraph uses three models, each for a different job:

| Model | Kind | Job |
|---|---|---|
| **Qwen3.5-9B** | Generative LLM (writes text) | Rewrite follow-ups, extract entities, write Cypher, write answers |
| **SapBERT** | Embedding model (turns names into vectors) | Entity linking: "heart attack" → "myocardial infarction" |
| **nomic-embed-text** | Embedding model | Choose the most similar example questions for the prompt |

---

## D12. Serve the LLM locally with Ollama

### What is it?

An LLM is a file of billions of numbers (weights) plus code that runs them. To use one on your own
machine you need a **model runtime**: software that loads the weights into memory (ideally onto the
GPU), turns text into tokens, runs the model, and returns generated text.

**Ollama** is a popular runtime for laptops and desktops. Its primary job: download a model with one
command and serve it through a simple local web API.

```
ollama pull qwen3.5:9b        # download (≈6.6 GB)
ollama run qwen3.5:9b         # chat in the terminal
ollama ps                     # what's loaded, how much memory, GPU or CPU
ollama stop qwen3.5:9b        # unload from memory
```

### How does it work?

- Ollama runs a server on `localhost:11434`.
- It uses the **llama.cpp** engine underneath, which is optimised for running quantized models (D14)
  on ordinary hardware, including Apple's Metal GPU on Macs.
- Models come as **GGUF** files: a single file with the quantized weights and the model's settings.
- When a request arrives, Ollama loads the model (if it isn't loaded), runs it and streams back tokens.
  It keeps the model in memory for a while afterwards (`keep_alive`) so the next request is fast.

### What it does in MediGraph

All LLM calls go through LangChain's `ChatOllama` client (`get_chat_model()` in
`backend/app/llm.py`), which sends HTTP requests to Ollama. nomic-embed-text also runs in Ollama.

### The decision and the reasoning

**Decision:** use Ollama.

**Reasoning:**

1. **It's the simplest way to run a quantized model on a Mac:** one-command install and download, GPU
   used automatically.
2. **A plain HTTP API** that LangChain supports directly, so the rest of the code doesn't care which
   runtime is behind it.
3. **Model management built in:** list, pull, stop, delete.
4. **Supports structured output** (JSON constrained to a schema, D23), which the entity-extraction
   step relies on.

### Alternatives

- **llama.cpp directly:** the engine Ollama uses. More control (e.g. cache slots), more setup.
- **vLLM:** a high-throughput server, the standard for NVIDIA GPU servers. Not designed for Macs.
- **MLX (Apple):** fast on Apple Silicon, but less tooling around serving and structured output.
- **Hugging Face Transformers directly in Python:** flexible, but unquantized models need far more
  memory and are slower on a laptop.

### Trade-offs

- **Less control.** For example, I tried giving each prompt type its own cache slot
  (`OLLAMA_NUM_PARALLEL=3`) to speed up repeated prompts; this Ollama version ignored the setting.
- **One more background app** using memory.

---

## D13. Use Qwen3.5-9B as the main model

### What is it?

**Qwen3.5** is a family of open-weight LLMs from Alibaba, released in 2026 under the Apache 2.0 license
("open-weight" means you can download and run the model yourself). The **9B** version has about 9
billion parameters (the adjustable numbers inside the model).

Why does parameter count matter? Roughly, more parameters means more capacity to store knowledge and
handle complex instructions, but also more memory and slower answers. The practical question is
always: what's the best model that fits my hardware?

### How does it work?

Like every modern LLM, Qwen is a **transformer** trained in stages:

1. **Pretraining:** read trillions of tokens and learn to predict the next one. This gives language
   skill and broad knowledge.
2. **Instruction tuning:** learn from examples of instructions and good responses, so it follows
   requests instead of just continuing text.
3. **Preference tuning:** learn from human (or AI) preferences between answers, so responses are more
   helpful and safer.

At use time, it produces text one token at a time, each time picking from a probability distribution
over its vocabulary (D15 covers how that choice is made).

### What it does in MediGraph

Four jobs: rewriting follow-up questions (`contextualize`), extracting entities with a scope decision
(`understand`), writing Cypher (`generate_cypher`), and writing the final answer (`answer`).

### The decision and the reasoning

**Decision:** Qwen3.5-9B for every LLM step.

**Reasoning:**

1. **Memory is the hard limit.** My laptop has 16 GB shared by the CPU, GPU, Neo4j, Docker, the
   browser and everything else. A 9B model at 4 bits is about 6 GB (D14). That's the largest size that
   leaves room for everything else.
2. **Among models that size, it's one of the strongest,** especially at code-like output (Cypher is
   code) and following detailed instructions.
3. **Apache 2.0 license:** free to use, including commercially.
4. **Structured output and tool support** in Ollama.
5. **The job doesn't need medical knowledge.** Facts come from the graph, so the model needs to be good
   at *reading, following rules and writing queries*, not at remembering medicine. That's why a general
   model is the right choice here.

### Alternatives

- **Llama 3.1 8B (Meta):** a well-known open model of similar size, already installed. Used as the
  comparison baseline (D44).
- **gpt-oss-20b (OpenAI's open-weight model):** a **mixture-of-experts** model, meaning it has many
  sub-networks ("experts") and uses only a few per token. Strong reasoning, but all experts must sit
  in memory: about 12–14 GB, too much alongside Neo4j.
- **Gemma (Google), small versions:** fit easily, but weaker at code-like tasks.
- **MedGemma (Google's medical model):** tuned on medical data. Doesn't help here, because medical
  facts come from the graph, and the 27B version is too large.
- **Neo4j's fine-tuned Text2Cypher models:** small models trained specifically to write Cypher.
  Promising for the query step, but I'd still need a general model for the other three jobs.
- **Qwen3.5-2B:** much faster, tested for the easy steps and rejected (D16).

### Trade-offs

- **Speed:** about 10–11 tokens per second on this laptop, so each LLM step takes 10–30 seconds.
- **A 9B model makes more mistakes** than frontier models. The pipeline compensates with entity
  linking, validation and retries (Part 5).

### Evidence

On the same 65 questions: Qwen3.5-9B got 96.0% exact result match vs 76.0% for Llama 3.1 8B, and
never needed the fallback (Llama needed it 23% of the time). Llama was faster (21 s vs 37 s median),
partly because its run happened after a restart when the laptop wasn't swapping.

### In an interview

"Memory decided the size: 16 GB shared with Neo4j means about 9B parameters at 4-bit. Within that,
Qwen3.5-9B is strong at structured, code-like output, which is what writing Cypher needs, and the
model doesn't need medical knowledge because facts come from the graph. Against Llama 3.1 8B on the
same questions it got 96% vs 76% exact match."

---

## D14. Use 4-bit quantization (Q4_K_M)

### What is it?

A model's weights are normally stored as 16-bit numbers (2 bytes each). **Quantization** stores them
with fewer bits, for example 4 bits each, by rounding every weight to one of a small set of levels and
keeping a scale factor to convert back.

Memory arithmetic for a 9-billion-parameter model:

| Precision | Bytes per weight | Memory |
|---|---|---|
| 16-bit | 2 | ~18 GB |
| 8-bit | 1 | ~9 GB |
| ~4.85-bit (Q4_K_M) | ~0.6 | ~5.5 GB |

### How does it work?

Take four weights `[0.12, −0.50, 0.31, 0.90]`. A 4-bit signed integer can hold −8 to 7.

```
scale = largest |weight| / 7 = 0.90 / 7 = 0.1286
store round(w / scale):  1, −4, 2, 7          (plus the one scale value)
read back q × scale:     0.129, −0.514, 0.257, 0.900
errors:                  0.009,  0.014, 0.053, 0.000
```

Real formats use a separate scale for every small block of weights (so one big weight only affects
its block), and **K-quant** formats like **Q4_K_M** keep the most sensitive parts of the model at
higher precision. The average comes to about 4.85 bits per weight.

### What it does in MediGraph

`ollama pull qwen3.5:9b` downloads a Q4_K_M GGUF file. `ollama ps` shows it using about 5.8 GB.

### The decision and the reasoning

**Decision:** use Ollama's default Q4_K_M build.

**Reasoning:**

1. **It's the only way a 9B model fits.** At 16-bit it would need 18 GB, more than the whole laptop.
2. **The quality loss is small.** Large networks have a lot of redundancy, so small rounding errors in
   individual weights mostly cancel out.
3. **A bigger quantized model usually beats a smaller full-precision one** in the same memory. A 9B
   model at 4-bit is generally better than a 4B model at 16-bit.

### Alternatives

- **8-bit (Q8_0):** nearly identical quality to 16-bit, but ~9 GB doesn't leave room for everything else.
- **3-bit or 2-bit:** smaller still, but quality drops noticeably.

### Trade-offs

- Some quality loss compared with full precision. I didn't measure it separately, because the
  full-precision model can't run on this machine.

---

## D15. Generation settings: temperature 0, 8K context, thinking off, keep loaded for 30 minutes

### What is it?

Each LLM call has settings that control how the model generates text. In `backend/app/llm.py`:

```python
ChatOllama(model="qwen3.5:9b", temperature=0, num_ctx=8192, reasoning=False, keep_alive="30m")
```

- **Temperature** controls randomness. At each step the model has a probability for every possible
  next token. Temperature 0 always picks the most likely one; higher values pick less likely tokens more
  often (more variety, more mistakes).
- **Context window (`num_ctx`)** is the maximum number of tokens the model can see at once: prompt
  plus answer.
- **Reasoning ("thinking") mode:** newer models like Qwen3.5 can write out hidden step-by-step
  reasoning before the final answer.
- **Keep-alive** is how long Ollama keeps the model in memory after a request.

### How does it work?

Temperature example: after "Aspirin is used to treat", suppose the model's probabilities are pain
61%, fever 22%, headache 14%, cancer 3%. At temperature 0 it always writes "pain". At temperature 2 the
distribution flattens and "cancer" goes from 3% to about 10%.

Context window: the model keeps a **KV cache** (stored intermediate results) for every token in the
window. For an 8B-class model that's roughly 128 KB per token, so 8,192 tokens is about 1 GB of extra
memory, and a 128K-token window would be far more.

### The decision and the reasoning

**Decision and reasoning, setting by setting:**

1. **Temperature 0.**
   - Writing Cypher has one correct answer; randomness only adds mistakes.
   - Evaluation needs **repeatable** results: the same question must produce the same query every time,
     otherwise comparing v1 and v2 would mix real changes with random noise.
2. **8K context.**
   - The prompts are 1,000–3,000 tokens (schema, rules, 4 examples, question; or rows for the answer).
   - A larger window means a larger KV cache and more memory pressure on a 16 GB laptop.
   - Models tend to use information in the middle of very long prompts less reliably.
3. **Thinking off.**
   - Thinking can add hundreds of tokens per call. At ~11 tokens per second that's tens of seconds,
     and there are 3–4 calls per question.
   - The pipeline already breaks the work into small, guided steps (find entities → write one query →
     answer from rows), which does the job thinking mode would do inside the model.
4. **Keep-alive 30 minutes.** Reloading a 6 GB model from disk takes seconds; keeping it loaded between
   questions avoids that.

### Alternatives

- **Small non-zero temperature** (e.g. 0.2): a little variety on retries, at the cost of repeatability.
- **Thinking on:** possibly fewer query mistakes on hard questions, at much higher latency. Not
  measured; a reasonable experiment for later.

### Trade-offs

- With temperature 0, retrying with the same prompt gives the same output. That's why retries always
  include the specific error message (D27): the prompt changes, so the answer can too.

---

## D16. Don't use a small "fast" model for the easy steps (tested, then reverted)

### What is it?

**Model routing** means sending each step to a different model depending on how hard it is: a small,
fast model for easy steps and a large model for hard ones. Done well, it saves time and money.

### How does it work in the code?

`_llm(config, fast=True)` in `nodes.py` can return a different model for the rewrite and extraction
steps, controlled by the `ollama_fast_model` setting. It's empty by default, which means "use the main
model for everything".

### The decision and the reasoning

**Decision:** use Qwen3.5-9B for every step; keep routing in the code but turned off.

**Reasoning (from an actual experiment):**

1. I routed follow-up rewriting and entity extraction to `qwen3.5:2b`, which was already installed.
2. Questions took **65–88 seconds** instead of ~45. Slower, not faster.
3. The reason was memory. On 16 GB of unified memory, having both models loaded pushed the Mac into
   more swapping (moving memory to disk), and every step waited on the disk.
4. The 2B model was also worse at the job: it ignored the instruction to skip generic words like
   "drugs" and "genes".
5. So I reverted, and documented the result next to the setting in `config.py`.

### Alternatives

- **Routing on a machine with more memory:** could genuinely help there.
- **Remove a step instead of making it smaller:** for example, skip entity extraction when the question
  already contains an exact node name. Listed as a future improvement.

### Trade-offs

- I kept the slower but simpler and, on this machine, actually faster setup.

### In an interview

"I tried routing easy steps to a 2B model. It made things slower, because two models in 16 GB of
unified memory caused more swapping, and the small model followed instructions worse. I measured it,
reverted, and documented why. On local hardware, memory is often the real bottleneck."

---

## D17. Keep an OpenAI option behind one setting

### What is it?

**LangChain** (D34) gives different model providers the same interface. `ChatOllama` and `ChatOpenAI`
both have `.invoke(messages)`, so code using one can use the other.

### How does it work?

```python
def get_chat_model(provider=None, model=None):
    if provider == "ollama": return ChatOllama(model=..., temperature=0, ...)
    if provider == "openai": return ChatOpenAI(model=..., api_key=...)
```

`LLM_PROVIDER=openai` plus `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env` switches every call.

### The decision and the reasoning

**Decision:** support both, local by default.

**Reasoning:**

1. **Comparisons:** I can run the same pipeline and evaluation against a stronger API model to see how
   much a better model would help.
2. **Deployment flexibility:** a hosted demo could use an API model without changing pipeline code.
3. **It costs nothing to keep:** a few lines.

### Trade-offs

- The OpenAI path isn't part of the main evaluation, so it's less tested.

---

## D18. Use SapBERT for entity-linking embeddings

### What is it?

An **embedding model** turns a piece of text into a fixed-length list of numbers (a vector) so that
texts with similar meaning get similar vectors. Closeness is measured with **cosine similarity**: 1
means pointing the same way (same meaning), 0 means unrelated.

**Entity linking** is matching a phrase in a question to the exact node in the database. Users say
"heart attack"; the graph says "myocardial infarction". If you can't make that match, even a perfect
query finds nothing.

**SapBERT** (Liu et al., 2021) is a biomedical embedding model on Hugging Face
(`cambridgeltl/SapBERT-from-PubMedBERT-fulltext`). It's built for exactly this problem.

### How does it work?

1. It starts from **PubMedBERT**, a BERT model trained on PubMed medical articles, so it already
   "speaks" medical language.
2. It's then trained on **UMLS**, a huge medical thesaurus that lists all the names of each medical
   concept ("myocardial infarction", "heart attack", "MI", "cardiac infarction" are one concept).
3. The training pulls names of the **same** concept close together and pushes names of **different**
   concepts apart, focusing on the hardest cases (like "heart attack" vs "heart failure").
4. The vector for a name is the output at BERT's special `[CLS]` position, which summarizes the input.

Real numbers from this project (cosine similarity with "heart attack"):

| Candidate | General model (nomic) | SapBERT |
|---|---|---|
| myocardial infarction (correct) | 0.848 | 0.778 |
| heart failure (wrong) | **0.892** | 0.578 |

The general model ranks the wrong answer first; SapBERT separates them by a wide margin.

### What it does in MediGraph

`SapBERTEmbeddings` in `backend/app/llm.py` loads the model with PyTorch and runs it on the Mac's GPU.
`setup.py` embeds about 85,000 node names once and stores the vectors in Neo4j (D11).
`linker.link_entity()` embeds each mention at question time and searches for the nearest names.

### The decision and the reasoning

**Decision:** SapBERT for entity linking.

**Reasoning:**

1. **Linking needs "same thing", not "related topic".** General embedding models are trained on web
   text, where "heart attack" and "heart failure" appear in the same articles, so they end up close.
   That's the wrong notion of similarity for linking.
2. **SapBERT was trained on exactly the right signal:** medical synonym sets.
3. **Wider score gaps** make it easier to set a threshold that rejects bad matches (D24).
4. **It's small** (~110 million parameters) and runs fast on the Mac GPU.

### Alternatives

- **nomic-embed-text** (general-purpose, already installed): fine for general similarity, poor at
  medical synonyms. Kept as a comparison and switchable with `ENTITY_EMBEDDER=nomic`.
- **Plain PubMedBERT or BioBERT:** medical vocabulary, but not trained to put synonyms together.
- **A synonym dictionary from UMLS:** precise where it has coverage, but needs a license and doesn't
  handle new phrasings.

### Trade-offs

- **Adds PyTorch and Transformers** (~650 MB in the Python environment) and a model download.
- **Brand names** (Tylenol, Zoloft) aren't in its training synonyms. The LLM's suggested standard term
  ("Acetaminophen") covers those (D23).

### Evidence

30 lay terms, vector search only (`eval/link_eval.py`): correct entity ranked first 57% (SapBERT) vs
27% (nomic); within the top 5, 80% vs 57%.

### In an interview

"Linking needs synonyms, not topical similarity. A general embedder put 'heart attack' closer to
'heart failure' than to 'myocardial infarction'. SapBERT is trained on medical synonym sets, and it
doubled first-choice accuracy on lay terms, 57% versus 27%."

---

## D19. Use nomic-embed-text to choose few-shot examples

### What is it?

**Few-shot examples** are solved examples included in a prompt ("Question: ... Cypher: ...") so the
model can copy the pattern (D22). Choosing *which* examples to include is itself a similarity search:
find the stored example questions most similar to the new question.

**nomic-embed-text** is a general-purpose embedding model that runs in Ollama.

### How does it work?

```
once:  embed all 22 example questions, normalise them           (examples._example_vectors, cached)
then:  embed the new question, compute cosine similarity with each, take the top 4
```

nomic expects a short **task prefix** on every text. For comparing a question with other questions
(symmetric similarity), the recommended prefix is `clustering:`, which `NomicEmbeddings` adds to both
sides.

### The decision and the reasoning

**Decision:** nomic for example selection, SapBERT for entity names.

**Reasoning:**

1. **Different job, different tool.** Comparing whole questions ("Which drugs are contraindicated in
   X?" vs "What drugs should not be given to people with Y?") is general sentence similarity, which
   nomic handles well. SapBERT is tuned for short entity names, not sentences.
2. **Already installed and tiny** (~300 MB), running inside Ollama.
3. **Brute force is fine:** 22 examples need no index.

### Trade-offs

- A second embedding model, which is a little more to explain, but it's cheap.

---

# Part 5: Pipeline design

The **pipeline** is the sequence of steps that turns a question into an answer. These are the
decisions that make a 9B model produce correct, safe database queries and honest answers.

```
contextualize → understand (entities + scope) → generate_cypher → validate → execute → answer
                       │                            ▲               │          │
                       └─ out_of_scope              └── retry ──────┴──────────┘
                                                          (≤ 3 attempts, then fallback / no_answer)
```

---

## D20. Have the LLM write a Cypher query (Text-to-Cypher)

### What is it?

Once you have a graph, something has to turn "What drugs treat gout?" into a database query.
**Text-to-Cypher** means the LLM writes that query. It's a kind of **semantic parsing** (turning
natural language into a formal program), the graph equivalent of **Text-to-SQL**.

```
Question:  What drugs treat gout?
Cypher:    MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: 'gout'})
           RETURN DISTINCT d.name AS drug LIMIT 50
```

### How does it work?

The model is given (D21–D22): the graph schema, the exact names of the entities in the question, a
few similar solved examples, and rules. It writes a query; the query is checked (D26) and run.

### What it does in MediGraph

`generate_cypher()` in `backend/app/pipeline/nodes.py` builds the prompt and calls the model;
`extract_cypher()` in `guards.py` pulls the query out of the reply (removing code fences, a "Cypher:"
prefix, a trailing semicolon).

### The decision and the reasoning

**Decision:** the LLM writes a query for every question.

**Reasoning:**

1. **Open-ended questions.** Users combine things in ways you can't list in advance: two hops,
   intersections ("drugs for both X and Y"), counts, yes/no, "which of those". A model writing queries
   handles combinations without me pre-building each one.
2. **The database does the hard work.** Joining, counting, filtering and de-duplicating are done
   exactly by Neo4j, not approximately by the model.
3. **Transparent.** The query is visible and can be checked by a person, by code, and by Neo4j itself.
4. **Reliability is recovered by checking.** Validation with precise error feedback and retries (D26,
   D27) fixes most of the model's mistakes.

### Alternatives

- **Intent classification + templates.** Classify the question into one of ~20 known types ("drugs
  for disease", "side effects of drug"...) and fill a pre-written query. Very reliable and fast, but
  answers only the question types you thought of.
- **Tool calling.** Give the model functions like `drugs_for(disease)` and let it choose and call
  them. Safe and controllable, same coverage limit as templates, and more model calls per question.
- **Subgraph retrieval.** Fetch everything around the mentioned entities, turn the edges into
  sentences, and let the model answer from those. No query writing, but around hub nodes the
  neighbourhood is thousands of edges, and counting is unreliable. Used only as the fallback (D29).
- **LangChain's `GraphCypherQAChain`.** A ready-made component that does question → Cypher → answer in
  one call. Quick to set up, but there's no place to add entity linking, validation, retries, a
  fallback, or per-step measurement.

### Trade-offs

- **Valid but wrong queries.** A query can pass every check and still answer a slightly different
  question, e.g. including off-label uses when asked about approved treatments. Only the evaluation
  catches these (D40).

### Evidence

94.8% of queries passed every check on the first attempt (v2). Retries fixed most of the rest.

### In an interview

"The LLM translates each question into Cypher. That handles open-ended combinations like two hops,
intersections and counts without writing a template for each, and the database does the joining and
counting exactly. Validation with error feedback recovers most of the reliability templates would give."

---

## D21. Describe the schema to the model by hand

### What is it?

The **schema** is the "shape" of the database: which node labels exist, what properties they have,
which relationship types exist, and which labels they connect. The model can only write correct
queries if it knows the schema.

There are two ways to give it: **introspection** (ask the database to list its own structure, e.g.
with the APOC plugin's `apoc.meta.data`, which LangChain's `Neo4jGraph` uses) or **a hand-written
description**.

### How does it work?

`backend/app/graph/schema.py` defines three Python structures (`NODE_LABELS`, `EXTRA_PROPERTIES`,
`RELATIONSHIPS`), and `schema_prompt()` renders them into text:

```
(:Disease) a disease or disorder (MONDO). Has `description` and `symptoms` text.
(:Drug)-[:INDICATED_FOR]->(:Disease)       // drug is an approved treatment for the disease
(:Drug)-[:OFF_LABEL_USE_FOR]->(:Disease)   // drug is used off-label for the disease
(:Drug)-[:CONTRAINDICATED_FOR]->(:Disease) // drug should not be used with the disease
```

### What it does in MediGraph

The rendered schema goes into every Cypher prompt. The same Python structures are used by the
validator (`check_schema` in `guards.py`) to detect unknown labels, unknown relationships and reversed
edges.

### The decision and the reasoning

**Decision:** a hand-written schema that is also the validator's source of truth.

**Reasoning:**

1. **Meaning, not just names.** Introspection would list `INDICATED_FOR` and `OFF_LABEL_USE_FOR` but
   not say which one answers "what treats X". The one-line comments give the model that meaning.
2. **Intended direction.** Introspection lists which labels a relationship connects, but the
   hand-written version states the direction the data actually uses, which the validator checks.
3. **No drift.** Because the prompt and the checks come from the same structures, they can never
   disagree.
4. **It's small.** 10 labels and 18 relationship types fit easily (~1,200 tokens for the whole prompt).

### Alternatives

- **Introspection** (APOC / LangChain): automatic and always current, but without meaning or direction.

### Trade-offs

- **Manual upkeep.** If the graph changed, the schema file would need editing. For a static dataset
  that's acceptable.

---

## D22. Dynamic few-shot: 22 hand-written examples, the 4 most similar in each prompt

### What is it?

**Few-shot prompting** means including a few solved examples in the prompt so the model can copy the
pattern. It works because LLMs are very good at continuing a pattern they've just seen.
**Dynamic** few-shot means choosing the examples for each question, by similarity, instead of always
showing the same ones.

### How does it work?

`backend/app/pipeline/examples.py` contains 22 question → Cypher pairs covering every pattern:
simple lookups, descriptions, counts, yes/no with `EXISTS`, two-hop paths, shared neighbours,
intersections, hierarchy, follow-ups. `select_examples(question)` embeds the question with nomic
(D19), compares it with all 22 example questions, and returns the 4 closest.

### What it does in MediGraph

For "Which medications are indicated for postmenopausal atrophic vaginitis?", the chosen examples are
other "drugs for a disease" questions, so the model sees exactly the pattern
`(d:Drug)-[:INDICATED_FOR]->(:Disease {name: ...})` right before writing its own.

### The decision and the reasoning

**Decision:** 22 hand-written examples, top 4 per question.

**Reasoning:**

1. **Small models copy patterns well.** Showing the right pattern is often more effective than
   explaining it in rules.
2. **Relevance beats quantity.** A prompt can only hold a few examples before it gets long and slow.
   Choosing the closest ones gives the most useful templates per token.
3. **Hand-written means correct.** Every example was run against the database. The model never learns
   a wrong pattern from them.
4. **Coverage was designed.** Each kind of question in the test set has at least one example pattern.

### Alternatives

- **Fixed examples:** simpler, but wastes prompt space on irrelevant patterns.
- **Many examples:** longer prompts, slower calls, less focus.
- **No examples:** more direction and pattern mistakes.
- **Examples generated by an LLM:** more of them, but some would be wrong.

### Trade-offs

- **Leakage risk.** If test questions used the same entities as the examples, the model could copy
  answers and the evaluation would look better than reality. Every example entity is excluded from the
  test set (D42).

---

## D23. One structured LLM call for entities, standard terms and scope

### What is it?

Before writing a query, the system needs to know: which medical things are mentioned, what the
standard name for each might be, what type each is, and whether the question can be answered at all.

**Structured output** means the model must reply with JSON matching a defined shape, not free text.
Ollama enforces this with **constrained decoding**: at every step, tokens that would break the JSON
shape are given zero probability, so the reply always parses.

### How does it work?

The shape is a Pydantic model (Pydantic is a Python library for defining and validating data shapes):

```python
class Mention(BaseModel):
    mention: str      # exact words from the question: "heart attack"
    canonical: str    # the standard medical term:     "myocardial infarction"
    type: str         # Disease, Drug, Gene, Phenotype, Exposure, Anatomy or Pathway

class Extraction(BaseModel):
    entities: list[Mention]
    in_scope: bool    # can the graph answer this?
    reason: str = ""  # why not, if out of scope
```

`llm.with_structured_output(Extraction).invoke(prompt)` returns an `Extraction` object.

### What it does in MediGraph

`understand()` in `nodes.py` makes this call, then links each mention to a graph node (D24) and stores
the scope decision for routing (D30).

### The decision and the reasoning

**Decision:** one structured call that does entity extraction, normalization and the scope check.

**Reasoning:**

1. **The LLM knows things embeddings don't.** "Tylenol is acetaminophen", "Lou Gehrig's disease is
   ALS": that's general knowledge the LLM has, and no embedding search reliably finds. The `canonical`
   field captures it.
2. **One call instead of two.** Extraction and scope could be separate calls, but each LLM call costs
   10–30 s on this laptop. Combining them saves a full call per question.
3. **Structured output makes it reliable.** The next step reads fields, not prose, so it can't break
   on unexpected wording.
4. **Graceful failure.** If anything goes wrong, the code continues with no entities and "in scope",
   and the query writer relies on the schema alone, rather than failing the whole request.

### Alternatives

- **A named-entity recognition model** (a model trained only to find entity spans, e.g. scispaCy):
  fast, but it doesn't suggest standard names or judge scope.
- **Separate calls for extraction and scope:** cleaner prompts, slower.
- **Skip extraction and rely on the query writer:** fewer calls, but no linking means made-up names.

### Trade-offs

- **It's the slowest step** (often 10–35 s), because the JSON reply is long.
- **The model can "over-normalize"** a precise name into a different one, which is handled in D24.
- **The prompt rule "skip generic words"** was added after "drugs" got linked to a node named "Drugs,
  Chinese Herbal".

---

## D24. Entity linking: exact first, then full-text and vector, re-scored, with a threshold, and the user's words win

### What is it?

**Entity linking** turns each mention into a specific graph node. It has two parts:
**candidate generation** (find possible nodes) and **ranking** (pick the right one, or decide none fits).

### How does it work?

`link_entity(db, mention, label)` in `backend/app/graph/linker.py`:

1. **Exact match.** Look the name up in the per-label name indexes, trying several casings: as typed,
   lower case (PrimeKG diseases are lower case), Capitalized (drugs), UPPER (gene symbols). If found,
   done: score 1.0, method "exact".
2. **Full-text candidates.** A Lucene search over the `entity_names` index. The query boosts the exact
   phrase (`"heart attack"^3`) and allows one-character typos on words longer than 4 letters
   (`heart~1 AND attack~1`).
3. **Vector candidates.** Embed the mention with SapBERT and fetch the nearest names from the vector
   index for the relevant label(s).
4. **Re-score every candidate on one scale:**
   `score = 0.85 × cosine(mention, candidate name) + 0.15 × string similarity`.
5. **Drop weak candidates:** keep only scores ≥ 0.55 (SapBERT) or ≥ 0.75 (nomic). Return the best 3.

Then in `understand()`:

6. **The user's own words win.** Link the user's exact words first. If they match a node exactly, use
   that and ignore the LLM's suggested `canonical` term. Otherwise, add candidates for the canonical
   term too, and put exact matches first.
7. If the top candidate isn't certain (score < 0.95), the next two names are passed to the query
   writer as "less likely" alternatives.

### What it does in MediGraph

The linked names go into the Cypher prompt ("heart attack" → `(:Disease {name: "myocardial
infarction"})`), so the model uses names that exist. The same function supplies suggestions when the
validator finds a made-up name (D26).

### The decision and the reasoning

**Reasoning, step by step:**

1. **Exact first** because it's instant and certain, and most names in questions are already correct.
2. **Two kinds of search because they fail differently.** Full-text (keyword) search catches typos
   and codes ("BRCA1") but not synonyms. Vector search catches synonyms but handles typos and codes
   less well. Together they cover more.
3. **Re-scoring because the raw scores aren't comparable.** Lucene scores and cosine similarities are
   on different scales. Before re-scoring, a fuzzy keyword hit like "diabetis" → "diabetic
   ketoacidosis" looked like a perfect match. Re-scoring everything with the same formula fixed that.
4. **Mostly meaning, a little spelling** (0.85 / 0.15). Meaning should dominate, but a small string
   bonus breaks near-ties between candidates.
5. **A threshold because guessing is worse than not linking.** Answering confidently about the wrong
   disease is the worst outcome. The thresholds differ per model because SapBERT spreads its scores
   much more widely than nomic.
6. **The user's words win because of a real failure.** In the first evaluation, the LLM "normalized"
   *Stanolone acetate* into *Stanozolol* (a different drug) and *Duane syndrome type 1* into the
   broader *Duane retraction syndrome*. The linker trusted the suggestion and answered about the wrong
   thing. If the user's words already name a node, they're the safest choice. The LLM's suggestion is
   only needed when they don't (Tylenol, heart attack).

### Alternatives

- **Vector search only:** misses typos and codes, and its scores alone can't tell "related" from "same".
- **Reciprocal rank fusion** (combine rankings by position instead of score): simpler, but ignores
  how strong each match is.
- **A cross-encoder reranker** (a model that scores each mention–candidate pair together): more
  accurate, but another model and slower.
- **Always trust the LLM's suggestion:** caused the Stanozolol error.

### Trade-offs

- The thresholds and weights were tuned by hand on a small set of examples.
- Re-scoring embeds each candidate name, adding a few milliseconds.

---

## D25. Rewrite follow-up questions into standalone questions

### What is it?

In a conversation, people refer back: "Which of **those** can cause bleeding?" A database query can't
use "those". **Query rewriting** turns a follow-up into a complete question using the conversation
history.

### How does it work?

`contextualize()` sends the last 6 turns (each cut to 800 characters) and the new question to the LLM
with `prompts.CONTEXTUALIZE`, which asks it to replace references with explicit names. If there's no
history, it skips the LLM call entirely.

```
History:   "What drugs treat a heart attack?" → "Alteplase, Reteplase, ..., Lisinopril"
Follow-up: "Which of those can cause bleeding?"
Rewritten: "Which of Alteplase, Reteplase, Tenecteplase, ..., Lisinopril can cause bleeding?"
```

### The decision and the reasoning

**Decision:** rewrite once at the start; every later step sees only the standalone question.

**Reasoning:**

1. **Every later step gets simpler.** Entity linking and query writing work on explicit names; no
   later prompt needs the conversation history.
2. **Shorter prompts** for the expensive steps.
3. **Free when not needed:** the first question in a conversation skips the call.

### Alternatives

- **Pass the full history to every step:** longer prompts, more confusion, slower.
- **Track the previous answer's entities in code** and inject them directly: more reliable for
  "those", listed as an improvement.

### Trade-offs

- One extra LLM call for follow-ups.
- **Known failure:** when "those" refers to many drugs, the query sometimes covered only the first one
  (seen once in the evaluation, not fixed yet).

---

## D26. Validate every query in layers before running it

### What is it?

The LLM's query will sometimes be wrong, and it could even be harmful (a `DELETE` inserted by a
malicious question, called **prompt injection**). **Validation** checks the query before it touches
the data. **Defence in depth** means using several independent checks, so if one has a gap, another
catches it.

### How does it work?

`validate()` in `nodes.py` runs, in order:

1. **Read-only text check** (`check_read_only` in `guards.py`):
   - First blank out everything inside quotes, so a phenotype named `'Set of symptoms'` doesn't look
     like the `SET` command.
   - Reject `;` (prevents "harmless query; harmful query").
   - Reject write and admin keywords: `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`, `REMOVE`, `DROP`,
     `LOAD CSV`, `FOREACH`, `GRANT`, `REVOKE`, `DENY`.
   - Reject `CALL` (running procedures) except the two read-only search procedures, and `CALL { }`
     subqueries.
2. **Schema check** (`check_schema`): unknown properties, labels or relationship types, with a "did
   you mean" suggestion; and edges written in the wrong direction, with the valid pattern spelled out.
3. **`EXPLAIN` in Neo4j** (`GraphDB.explain`): Neo4j plans the query without running it. Syntax errors
   appear here, and Neo4j reports the query type. Only `r` (read) passes.
4. **Names exist** (`check_entity_names`): every quoted name (`{name: 'X'}`, `x.name = 'X'`,
   `x.name IN [...]`) must be a real node of that label; otherwise the error lists the closest real
   names from the linker.
5. **At execution** (`GraphDB.read`): the query runs in a **READ access-mode session** (Neo4j refuses
   any write), with a 10-second timeout and a 50-row limit.

Example error message the model receives:

```
(:Disease)-[:INDICATED_FOR]->(:Drug) does not exist. Valid: (:Drug)-[:INDICATED_FOR]->(:Disease).
```

### The decision and the reasoning

**Decision:** all five layers.

**Reasoning:**

1. **Each layer catches different things.**
   - Text check: obvious writes, cheaply, before any database call.
   - Schema check: the model's most common mistakes, with messages it can act on.
   - EXPLAIN: syntax errors and anything that writes, judged by Neo4j's own parser, which can't be
     fooled by formatting tricks that might fool a regular expression.
   - Name check: plausible-but-invented names ("Bleeding"), which run fine and silently return nothing.
   - READ session: enforced by the database server itself, regardless of what the model wrote.
2. **Good errors make good retries.** The point isn't only to block bad queries; it's to tell the model
   precisely what to fix (D27).
3. **Least privilege.** The pipeline can only ever read. Even a successful prompt injection can't
   change data.

### Alternatives

- **Only a regular expression:** easy to miss something.
- **Only the READ session:** safe, but a failed query gives the model no useful feedback.
- **A read-only database user:** the cleanest guarantee, but needs Neo4j Enterprise.

### Trade-offs

- About 0.2–0.9 seconds per validation (EXPLAIN and name lookups).
- Regular expressions need maintenance and tests: 22 unit tests in `backend/tests/test_guards.py`.

### In an interview

"Generated queries go through five layers: a text check for write clauses, a schema check for made-up
labels and reversed edges, Neo4j's EXPLAIN which must report a read-only query, a check that every
entity name exists, and finally execution in a read-only session with a timeout and row cap. The first
four also produce precise error messages the model uses to fix the query."

---

## D27. Retry with the error message, at most 3 attempts

### What is it?

**Self-correction** means letting the model fix its own mistake by showing it what went wrong. The
key is giving it the specific error, not just asking again.

### How does it work?

On a retry, `generate_cypher()` sends a short conversation:

```
system: schema, rules, linked entities, examples
user:   Question: What is Klinefelter syndrome treated with?
AI:     MATCH (d:Disease {name:'Klinefelter syndrome'})-[:INDICATED_FOR]->(dr:Drug) ...
user:   Your previous query failed. Problems:
        (:Disease)-[:INDICATED_FOR]->(:Drug) does not exist. Valid: (:Drug)-[:INDICATED_FOR]->(:Disease).
        Write a corrected query.
```

The routing functions in `workflow.py` send the flow back to `generate_cypher` while
`attempts < max_cypher_attempts` (3), then to the fallback.

### The decision and the reasoning

**Decision:** up to 3 attempts, each with the previous query and its errors.

**Reasoning:**

1. **Models fix specific errors well.** "This edge goes the other way" is easy to act on.
2. **Temperature 0 needs a changed prompt.** Without the error message, the same prompt would produce
   the same wrong query (D15).
3. **A cap bounds latency and prevents loops.** Each attempt costs 10–30 s. If three attempts with
   feedback fail, a fourth rarely helps; falling back honestly is better.

### Alternatives

- **No retries:** simpler, but every model mistake becomes a failed answer.
- **Unlimited retries:** risk of long or endless loops.

### Evidence

The Klinefelter example above failed on attempt 1 and succeeded on attempt 2. In v2, 94.8% of queries
were valid first time.

---

## D28. Retry once when a valid query returns nothing; stop if the model stands by it

### What is it?

A query can be perfectly valid, run fine, and return **no rows**. That happens for two very different
reasons: the query is subtly wrong, or the answer genuinely isn't in the graph.

### How does it work?

1. The first time `execute()` gets an empty result, it sets an error ("The query ran but returned no
   results. Check relationship directions and entity names. If the query is correct, return it
   unchanged.") and marks `empty_retry_done`.
2. The model gets one more attempt with that message.
3. If it returns **exactly the same query**, `_after_generate()` treats that as "I'm confident" and
   goes straight to the fallback instead of re-running it.
4. If a second attempt is also empty, the flow goes to the fallback.

### The decision and the reasoning

**Reasoning:**

1. **One retry catches the subtle-mistake case** (wrong direction on an undirected-looking pattern, the
   wrong one of two similar entity names).
2. **Only one,** because when the answer truly isn't in the graph, more retries just waste time.
3. **Detecting an unchanged query** avoids running the same empty query twice.

### Trade-offs

- One extra LLM call on questions whose answer genuinely isn't in the graph.

---

## D29. A neighbourhood fallback, then a fixed "not found" message

### What is it?

When the exact query can't find an answer, the system still has two options better than guessing:
show what the graph *does* know about the entities, or say plainly that it doesn't know.

### How does it work?

`fallback()` in `nodes.py`, for up to 3 linked entities:

1. Runs a query listing the entity's relationships: type, direction, the kind of node on the other
   end, how many, and up to 8 example names.
2. Fetches the entity's description, mechanism and symptoms text.

If that finds anything, the answer step runs with an extra instruction (`prompts.FALLBACK_NOTE`): say
the exact relationship wasn't found, then summarize what is known. If it finds nothing at all,
`no_answer()` returns a **fixed sentence** (`prompts.NO_ANSWER`), not model-written text.

### The decision and the reasoning

**Reasoning:**

1. **"Not found, but here's what the graph knows" is useful and honest.** For "Which of these drugs
   cause bleeding?" with no matching side effect, the user still learns what the graph contains.
2. **A fixed message can't hallucinate.** When there's truly nothing, no model text is generated.
3. **The model is told explicitly to say the exact fact wasn't found,** so the fallback answer can't
   pretend to be a direct answer.

### Alternatives

- **Let the model answer from memory when the graph is empty:** exactly what the project avoids (D1).
- **Always return "not found":** honest, but throws away useful related information.

### Trade-offs

- **Lookalike entities.** For made-up names that weakly match a real one ("Floxabutrin" → "Floxuridine"),
  the answer says the original wasn't found but then talks about the lookalike. A "did you mean?" step
  would be cleaner (listed as an improvement).

### Evidence

All 12 must-decline test questions (made-up diseases, dosing, prices, off-topic) were declined or
answered "not found" in both versions.

---

## D30. Check scope before retrieving anything

### What is it?

Some questions shouldn't be answered by this system at all: personal medical advice ("What dose
should I take?", "Should I stop my medication?") and questions about data the graph doesn't hold
(prices, prevalence statistics, clinical-trial results). A **scope check** decides that up front.

### How does it work?

The `in_scope` field from the extraction call (D23). `_after_understand()` in `workflow.py` routes
out-of-scope questions straight to `out_of_scope()`, which returns a polite template message that
explains what the system can answer and suggests talking to a clinician.

### The decision and the reasoning

**Reasoning:**

1. **Safety:** the system should never appear to give personal medical advice.
2. **Efficiency:** declining early skips 2–3 expensive LLM calls.
3. **Clarity:** the user learns what kinds of questions work.

### The rules, and how they were tuned

The first version was too cautious. Error analysis (D46) found it refusing:

- "Which drugs should be **avoided** in patients with X?" (treated as personal advice)
- "**How many** drugs are indicated for X?" (treated as a statistics request)

I rewrote the rules: general questions about conditions and drugs are in scope, including
contraindications and counts; only questions about the asker's own care, or data the graph lacks, are
out. A later run showed one new false refusal: the model decided "H TYPE II TRISACCHARIDE" "isn't a
real drug". The prompt now says not to judge whether an entity is real; the database decides that.

### Trade-offs

- An LLM judgement can be wrong in both directions. It needs evaluation to tune (D46).

### Evidence

Wrong refusals: 3.0% (v1) → 0.7% (v2). Must-decline questions declined: 100% in both.

---

## D31. Limit how much data reaches the answer prompt

### What is it?

The model's context window is limited (8K tokens, D15), and long prompts are slow and make models less
focused.

### How does it work?

- Queries return at most **50 rows** (`max_result_rows`), and the answer is told when the list was cut.
- Text fields longer than **500 characters** are shortened (`_compact()` in `nodes.py`).
- The serialized rows are cut at **12,000 characters**.

### The decision and the reasoning

**Reasoning:**

1. **Everything must fit** in the window with the rules and the question.
2. **Less irrelevant text means better answers.** Models use information in the middle of long prompts
   less reliably.
3. **50 is a readable list.** Nobody wants a 188-item side-effect list in a chat answer.

### Trade-offs

- Long lists are partial. The answer says "the list is partial", and the evaluation scores recall at 50
  items to match (D43).

---

## D32. Show the evidence with every answer

### What is it?

Next to each answer, the UI shows the **pipeline steps** (what was linked, each query attempt and its
errors), the **Cypher**, the **raw rows**, and a **graph picture** of the entities involved.

### How does it work?

Each node adds a trace entry with timing and details; the API streams them to the browser (see
`docs/flow.md`). `build_subgraph()` in `nodes.py` looks up the nodes mentioned in the evidence and the
edges between them for the picture.

### The decision and the reasoning

**Reasoning:**

1. **Grounding only matters if it can be checked.** Showing the query and rows lets anyone verify any
   claim.
2. **Debugging becomes fast.** You can see immediately whether a bad answer came from linking, the
   query, or the answer step.
3. **It builds appropriate trust:** users see exactly what the answer rests on.

### Trade-offs

- Two extra small database queries per answer for the subgraph (milliseconds).

---

# Part 6: Application and tooling

The software around the pipeline: how the steps are wired together, how the browser talks to the
server, and the tools that keep the code reproducible and tested.

---

## D33. Use LangGraph to orchestrate the pipeline

### What is it?

**Orchestration** is the code that decides which step runs next and passes data between steps. For a
straight line of steps, a few function calls are enough. MediGraph's flow isn't a straight line: it
has **loops** (retry until the query is valid) and **branches** (decline, fall back, give up).

**LangGraph** is a library for building LLM applications as a **state machine**:

- A **state** is a dictionary shared by all steps (question, entities, query, rows, answer...).
- **Nodes** are ordinary Python functions. Each reads the state and returns only the fields it changed.
- **Edges** connect nodes. **Conditional edges** call a small function that looks at the state and
  returns the name of the next node.

A state machine is like a board game: you're always on one square (node), the square tells you what
to do, and a rule (edge) tells you where to move next based on the current situation.

### How does it work?

```python
g = StateGraph(GraphRAGState)                 # the shape of the shared state
g.add_node("validate", nodes.validate)        # a step
g.add_conditional_edges("validate", _after_validate, ["execute", "generate_cypher", "fallback"])

def _after_validate(state):                   # a routing rule
    if not state["errors"]:
        return "execute"
    return "generate_cypher" if state["attempts"] < 3 else "fallback"

workflow = g.compile()
workflow.invoke({"question": "...", "history": []})        # run to the end
workflow.astream(inputs, stream_mode=["updates", "messages"])  # run and stream progress
```

Two more features MediGraph uses:

- **Reducers.** Normally a node's returned value replaces the field. A field declared as
  `Annotated[list, operator.add]` is *appended to* instead. `trace` and `attempt_log` use this, so
  every step's record is kept.
- **Run configuration.** `config={"configurable": {"model": "llama3.1:latest"}}` changes settings for
  one run. The evaluation uses this to run the same graph with a different model.

### What it does in MediGraph

`backend/app/pipeline/workflow.py` builds the graph (9 nodes, 5 routing functions);
`backend/app/pipeline/state.py` defines the state; `backend/app/pipeline/nodes.py` holds the steps.

### The decision and the reasoning

**Decision:** LangGraph.

**Reasoning:**

1. **Loops and branches are explicit.** Every decision ("retry or give up?") is a small, readable
   function, instead of being buried in nested `while` and `if` blocks.
2. **Streaming for free.** `astream` emits each node's result as it finishes and the LLM's tokens as
   they're generated, which the UI shows live (D35).
3. **Tracing.** Each node's trace entry is collected automatically through the reducer, giving a full
   record of every question for the UI and the evaluation.
4. **Swappable models per run** through `configurable`, without global changes.
5. **Safety net:** `recursion_limit` stops runaway loops even if a routing bug slipped in.

### Alternatives

- **Plain Python** (functions and while-loops): fewer dependencies, fully transparent, but I'd rebuild
  streaming and tracing myself.
- **A LangChain chain:** good for straight pipelines, awkward for loops.
- **A free-form agent** (the LLM decides which tool to call next): flexible, but unpredictable, slower,
  and much harder to evaluate. MediGraph's flow is fixed by design; only the query content varies.

### Trade-offs

- **A framework to learn** (state, reducers, stream modes), and another dependency to keep up to date.

### In an interview

"The pipeline has loops and branches: retry until the query is valid, decline, fall back. LangGraph
models that as a state machine where each routing decision is a small function, and it streams every
step's result and the answer tokens, which my UI shows live."

---

## D34. Use LangChain only for model clients and structured output

### What is it?

**LangChain** is a large library of building blocks for LLM apps: model clients for many providers,
prompt templates, chains, retrievers, agents and more.

### How does it work in MediGraph?

Only a small part is used:

| Piece | Used for |
|---|---|
| `ChatOllama`, `ChatOpenAI` | Calling models through one interface (D17) |
| `SystemMessage`, `HumanMessage`, `AIMessage` | Building chat prompts, including retry conversations |
| `.with_structured_output(Model)` | Getting JSON that matches a Pydantic shape (D23) |
| `Embeddings` base class | `NomicEmbeddings` and `SapBERTEmbeddings` share one interface |

### The decision and the reasoning

**Reasoning:**

1. **Get the useful parts for free:** provider switching and structured output are fiddly to write.
2. **Keep control of the pipeline:** the ready-made graph Q&A chain couldn't host linking, validation,
   retries or tracing (D20).

### Trade-offs

- LangChain packages change often; exact versions are pinned in `uv.lock` (D38).

---

## D35. Serve the pipeline with FastAPI and stream progress with server-sent events

### What is it?

The browser can't run Python, so the pipeline runs on a **server**: a program that waits for requests
over HTTP (the protocol web browsers use) and sends back responses. **An API** is the set of URLs the
server answers, like `POST /api/chat`.

**FastAPI** is a modern Python web framework. Key features:

- You write normal Python functions and decorate them with the URL they answer.
- Request bodies are checked automatically against **Pydantic** models; bad requests get a clear
  `422` error before your code runs.
- It supports `async` code, so one server can handle many waiting requests.
- It generates interactive API documentation at `/docs`.

**Server-sent events (SSE)** let a server keep one HTTP response open and send messages through it as
things happen. Each message looks like:

```
event: step
data: {"step": "understand", "ms": 15234, ...}

```

### How does it work?

```python
class ChatIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    history: list[TurnIn] = Field(default_factory=list, max_length=20)

@app.post("/api/chat")
async def chat(body: ChatIn):
    async def stream():
        async for mode, chunk in workflow.astream(...):
            ...                                    # turn pipeline updates into events
            yield {"event": "step", "data": json.dumps(trace_entry)}
        yield {"event": "final", "data": json.dumps({...})}
    return EventSourceResponse(stream())           # from the sse-starlette package
```

The browser reads the events one by one and updates the screen (see `docs/flow.md`).

### The decision and the reasoning

**Decision:** FastAPI with an SSE stream.

**Reasoning:**

1. **Answers take ~40 seconds.** Without streaming the user would stare at a spinner. With SSE they see
   each step (entities found, query written, validated, rows returned) and the answer as it's written.
2. **SSE is the simplest fit.** Updates only flow from server to browser, and SSE is plain HTTP: no
   special server, easy to debug with `curl`.
3. **FastAPI validates input for free,** which is also a safety measure: questions are limited to 1,000
   characters and history to 20 turns.
4. **Async** lets the server keep handling other requests while one waits on the LLM.

### Alternatives

- **WebSockets:** a two-way, always-open connection. More than needed here, and more complex.
- **Polling:** the browser asks "done yet?" every second. Wasteful and laggy.
- **Streamlit or Gradio:** build a UI in pure Python very quickly, but with little control over the
  streaming layout and the evidence panel.
- **Flask:** a simpler, older framework without native async or built-in validation.

### Trade-offs

- The browser's built-in `EventSource` only supports GET requests, but the question and history need
  a POST body. The frontend reads the stream manually in `frontend/src/api.ts`.

---

## D36. Build the interface with React, TypeScript and Vite, and draw the graph with vis-network

### What is it?

- **React** is a JavaScript library for building user interfaces from **components** (reusable pieces
  like a chat message or a table) and **state** (data that, when it changes, makes the UI re-render).
- **TypeScript** is JavaScript with types, which catches many mistakes before running.
- **Vite** is the development server and build tool; it reloads the page instantly when code changes
  and **proxies** `/api` requests to the Python server on port 8000.
- **vis-network** is a library that draws node-and-edge diagrams with a physics layout.

### How does it work in MediGraph?

`frontend/src/App.tsx` holds the chat on the left and the evidence panel on the right (Pipeline,
Cypher, Graph, Rows tabs). Each assistant message keeps its own evidence, so clicking an older answer
shows how it was produced. `GraphView.tsx` draws the subgraph with entities coloured by type.

### The decision and the reasoning

**Reasoning:**

1. **Showing evidence is a core feature** (D32), and it needs a real, interactive UI with tabs, live
   updates and a graph view.
2. **Live updates** from the SSE stream map naturally onto React state.
3. **vis-network** gives a usable graph drawing in a few dozen lines.

### Alternatives

- **Streamlit / Gradio:** much faster to build in Python, much less control.
- **Plain HTML and JavaScript:** no build step, but more manual work for live updates.

### Trade-offs

- A second toolchain (Node.js and npm) in addition to Python.

---

## D37. Typed configuration in `.env`, and expensive objects created once

### What is it?

**Configuration** is everything that might change between machines or runs: which model, the database
password, limits. Keeping it out of the code means you change settings without editing code, and
secrets stay out of git.

**Caching a function** with `@lru_cache` (from Python's standard library) means the first call runs
the function and every later call returns the same result. It's a simple way to create one shared
object (a **singleton**).

### How does it work?

```python
class Settings(BaseSettings):                       # pydantic-settings reads .env
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_model: str = "qwen3.5:9b"
    max_cypher_attempts: int = 3
    ...

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

The same pattern makes `get_db()` (the Neo4j connection pool), `get_entity_embeddings()` (SapBERT,
which takes seconds to load), `get_embeddings()`, and `build_workflow()` (the compiled graph) each
create their object once.

### The decision and the reasoning

**Reasoning:**

1. **Typed settings catch mistakes early.** `LLM_PROVIDER=olama` (a typo) fails at startup instead of
   failing on the first question.
2. **Secrets stay out of git.** `.env` is git-ignored; only `.env.example` with blank values is committed.
3. **No repeated expensive work.** Loading SapBERT or opening database connections once per process,
   not once per question.

### Trade-offs

- Changing `.env` requires restarting the server.

---

## D38. Use uv for the Python environment, ruff for linting, pytest for tests

### What is it?

- A **virtual environment** is a private folder of Python packages for one project, so projects don't
  interfere with each other or with your system Python.
- **uv** creates the environment, installs packages very quickly, can download the right Python
  version, and writes a **lockfile** (`uv.lock`) recording the exact version of every package.
- **ruff** is a **linter**: it scans code for problems like unused imports, undefined names and style
  issues.
- **pytest** runs **unit tests**: small functions that check one behaviour each.

### How does it work in MediGraph?

```
uv sync          # build .venv exactly as in uv.lock (Python 3.12, pinned in .python-version)
uv run ...       # run a command inside that environment
make lint        # ruff check backend eval
make test        # pytest: 22 tests for backend/app/pipeline/guards.py
```

### The decision and the reasoning

**Reasoning:**

1. **Reproducible environment.** `uv sync` rebuilds the same environment on any machine. The system
   Anaconda Python on the laptop stays untouched.
2. **Tests target the security code.** `guards.py` uses regular expressions to block write queries;
   regular expressions are easy to get subtly wrong. Each test pins one behaviour, e.g. "a write
   keyword inside a quoted string is allowed" or "a reversed edge is detected".
3. **Linting** catches simple mistakes before they become bugs.

### Alternatives

- **pip + venv / conda / Poetry:** all work; uv is much faster and handles the Python version too.

### Trade-offs

- **No unit tests for the LLM-dependent steps.** Their behaviour is covered by the evaluation instead
  (Part 7), because their output depends on the model.

---

## D39. A Makefile for common commands

### What is it?

**Make** is an old, standard tool for naming commands. A `Makefile` lists short targets and the
commands they run.

### How does it work?

```
make install   make data   make import   make up   make setup
make api       make ui     make test     make lint make eval
```

### The decision and the reasoning

**Reasoning:** short, memorable commands, and one place that documents how to run everything.
Someone cloning the repo can read the Makefile and understand the whole setup order.

---

# Part 7: Evaluation

Evaluation is how I know the system works, how much each part helps, and what to fix. These are the
decisions behind the test set, the metrics and the comparisons.

---

## D40. Build my own test set, with exact correct answers from the graph

### What is it?

An **evaluation set** (or "golden set") is a list of test questions with known correct answers. You
run the system on all of them and score the results automatically.

The main choice is **how you decide whether an answer is right**:

- **Exact gold answers:** you know the exact correct answer and compare against it.
- **LLM-as-judge:** another (usually stronger) LLM reads the answer and grades it.
- **Human grading:** people read and grade answers.

### How does it work in MediGraph?

`eval/build_golden.py` creates 147 questions. For most of them, the correct answer is computed by
running a **reference Cypher query** that I wrote by hand. For "What drugs are used to treat gout?",
the gold answer is exactly the set of drugs the reference query returns.

### The decision and the reasoning

**Decision:** my own set, with gold answers from reference queries.

**Reasoning:**

1. **Exact gold means exact scoring.** No opinions, no second model, no randomness: a drug is either in
   the correct set or it isn't.
2. **It measures the right thing.** The question for a grounded system is "did it answer faithfully
   from its source?" Gold answers from the same source measure exactly that.
3. **It's cheap and repeatable.** I re-ran it after every change.

### Alternatives

- **LLM-as-judge** (tools like RAGAS): convenient for free-text answers, but judges have known biases
  (favouring longer answers, the first answer shown, and their own style), and the judge can be wrong.
- **Medical exam benchmarks** (e.g. MedQA): test a model's medical knowledge, not faithfulness to a
  graph.
- **Neo4j's Text2Cypher dataset:** tests query writing, but on other databases' schemas.

### Trade-offs

- **Faithfulness, not truth.** Because gold comes from PrimeKG, a perfect score means "faithful to
  PrimeKG", not "medically complete".
- **Template phrasing** is more regular than real users (D41).

### In an interview

"I built 147 test questions whose correct answers come from reference Cypher queries, so scoring is
exact with no LLM judge. That measures faithfulness to the knowledge graph, which is what a grounded
system should be judged on; I'm explicit that it doesn't measure medical truth."

---

## D41. Generate most questions from templates, and hand-write the special cases

### What is it?

A **template** is a question pattern with a blank: "What drugs are used to treat {disease}?". Filling
the blank with many real diseases gives many test questions quickly.

### How does it work?

For each of 8 list categories (drugs for disease, side effects, disease genes, drug targets,
contraindications, symptoms, and two kinds of two-hop question):

1. Run the reference query for **every** candidate entity and keep only those with a reasonable answer
   size (e.g. 3–30 drugs), so answers are checkable and not empty.
2. Remove names that are awkward (brackets, slashes, over 45 characters) or that appear in the prompt
   examples (D42).
3. Shuffle with a fixed random **seed** (7) and take 12, phrasing each with one of 2–3 templates.

Plus: 8 counts, 10 yes/no questions (5 yes, 5 no), and hand-written groups: 11 lay-term questions
("side effects of Zoloft"), 10 follow-ups with the first turn given as history, and 12 questions the
system must decline (dosing, prices, made-up diseases, off-topic).

### The decision and the reasoning

**Reasoning:**

1. **Coverage:** every query pattern and every behaviour I cared about, including declining and
   follow-ups.
2. **Scale with little effort:** templates give 96 list questions with exact answers.
3. **Reproducible:** the fixed seed means anyone gets the same 147 questions.
4. **Hand-written where templates can't help:** lay terms, conversations and refusals need human design.

### Trade-offs

- **147 questions is small.** It gives confidence intervals of about ±4 points on the main metrics
  (D46). Detecting small improvements would need around a thousand questions.

---

## D42. Keep the prompt's example entities out of the test set

### What is it?

**Data leakage** is when information from the test set gets into what the system learns from, making
scores look better than reality. Here, the "learning" is the few-shot examples in the prompt (D22).

### How does it work?

`build_golden.py` collects every entity name quoted in the example queries (asthma, Ibuprofen,
Alzheimer disease...) plus a few extras into a `LEAKED` set, and skips any subject in it.

### The decision and the reasoning

**Reasoning:** if the test asked "What are the side effects of Ibuprofen?" and the prompt contained
exactly that example query, the model could copy it, and the score would measure copying rather than
query writing.

---

## D43. Define the metrics precisely, with "unsupported claims" as the headline

### What is it?

A **metric** is a number that measures one aspect of quality. Two basic ones for lists:

- **Precision:** of the items the system named, how many were correct?
- **Recall:** of the correct items, how many did the system name?

Example: the correct answer has 10 drugs; the system names 8, of which 7 are right. Precision = 7/8 =
87.5%, recall = 7/10 = 70%.

### How does it work in MediGraph?

All in `eval/metrics.py`:

| Metric | Meaning |
|---|---|
| **Answer recall@50** | Correct items named in the answer ÷ the number of correct items (at most 50, because the pipeline returns at most 50 rows) |
| **Unsupported claims** | Items named in the answer that appear in none of: the correct answer, the retrieved rows, the question. *Claims with nothing behind them.* |
| **Off-gold** | Items named that aren't in the correct answer (stricter: also counts grounded extras like off-label drugs) |
| **Exact match** | The query returned exactly the correct set |
| **Valid first try** | The first query passed every check |
| **Count / yes-no correct** | The right number, or the right yes/no |
| **Refusal correct / false refusal** | Declined when it should / declined when it shouldn't |
| **Latency p50 / p95** | Median time, and the time 95% of questions finish within |

Items are found by searching the answer text for every node name of the right type (no LLM involved).

### The decision and the reasoning

**Reasoning:**

1. **Unsupported claims is the headline** because, in medicine, a wrong claim (a precision error) is
   more dangerous than a missing item (a recall error). It's also exactly what hallucination means.
2. **Compare result sets, not query text.** Two different queries can both be right. Comparing what
   they *return* accepts any correct query.
3. **Recall at 50** because the pipeline returns at most 50 rows by design. Without the cap, a question
   with 188 correct answers could never score above 27%.
4. **Deterministic matching** is cheap, transparent and identical for every system.
5. **Refusal metrics in both directions.** Declining everything would score perfectly on "declines
   correctly", so false refusals must be measured too.

### Trade-offs

- **Name matching** can miss paraphrased names and can't judge explanatory text.

---

## D44. Compare against baselines, on equal terms

### What is it?

A **baseline** is a simpler or alternative system you compare against. "93% exact match" means nothing
alone; "93% vs 76% for Llama, and 0% vs 39% unsupported claims without the graph" means a lot. An
**ablation** removes or swaps one part to measure what it contributes.

### How does it work?

| Comparison | Measures |
|---|---|
| Same model, no graph (closed-book), 147 questions | What grounding adds |
| Llama 3.1 8B in the same pipeline, 65 questions | Whether the model choice matters |
| nomic vs SapBERT, linking only | What the medical embedder adds |
| v1 vs v2 | Whether the fixes from error analysis helped |

### The decision and the reasoning

**Reasoning:**

1. **Each comparison answers one question** about one decision.
2. **Equal terms.** The Llama run used a balanced 65-question subset (5 per category) to save an hour
   of laptop time, so Qwen was re-scored on exactly the same 65 for the comparison. Same prompts, same
   scoring code.

### Trade-offs

- The subset gives Llama wider uncertainty.
- The Llama run happened after a restart, on a much less loaded laptop, so its **speed** advantage is
  partly the machine. Accuracy isn't affected by that.

---

## D45. Fix the measurement when it's wrong, never to get a better number

### What is it?

Metrics are code, and code has bugs. A buggy metric makes you "fix" things that aren't broken.

### How does it work?

Reading individual failures (instead of only the averages) revealed several scoring bugs:

| Bug | Effect | Fix |
|---|---|---|
| "Estradiol" inside "Estradiol acetate" | Correct answers undercounted | Match longest names first and blank them out |
| Symptom names that are common words ("Severe", "Pain") | Ordinary words counted as claims | Case-sensitive matching for single-word names when counting extras |
| The question's own subject | "Ampicillin" counted as a claim in "drugs sharing a target with Ampicillin" | Anything in the question isn't an unsupported claim |
| Symptoms quoted from a retrieved description | Counted as unsupported | Unsupported = not in gold, rows *or* question |
| Correct answers longer than 50 items | Impossible to score well | Recall at 50 |
| "...not included in the data" in count answers | Correct answers counted as refusals | Detect prose refusals only for list questions with no names given |

### The decision and the reasoning

**Reasoning:**

1. **Every fix makes the metric match its definition,** and is applied identically to every system, so
   it can't favour one.
2. **Each fix is documented** at the top of `metrics.py`.
3. **The principle:** change the metric only when it measures the wrong thing; never to improve a score.

---

## D46. Use error analysis to decide what to fix, and re-run everything

### What is it?

**Error analysis** is reading every failure, grouping failures by cause, and fixing the biggest groups
first. It's how you decide what to work on based on evidence instead of guesses.

### How does it work?

After v1, I grouped the failures:

| Cause | Cases | Fix |
|---|---|---|
| Scope check refused "drugs to avoid" and "how many" | 4 | Rewrote the scope rules (D30) |
| LLM renamed a precise name into a different one | 3 | The user's exact term wins (D24) |
| Long lists shortened in the answer | 3 | "Include every retrieved item" rule |
| Follow-up query covered only the first of several drugs | 1 | Not fixed yet |

Then I re-ran all 147 questions, which caught one new problem (the "not a real drug" refusal, D30).

### The decision and the reasoning

**Reasoning:**

1. **Fix causes, not cases.** Each fix is a general rule, not a special case for one question.
2. **Re-run everything** to catch regressions; one appeared and was fixed.
3. **Be honest about significance.** With 147 questions, the overall exact-match change (91.5% →
   93.2%) is within the ±4-point uncertainty, so it isn't statistically significant on its own. The
   targeted fixes clearly worked: every refused contraindication and count question was answered in v2.

### Evidence

| Metric | v1 | v2 |
|---|---|---|
| Answer recall@50 | 94.0% | 96.8% |
| Exact match | 91.5% | 93.2% |
| Counts and yes/no correct | 94.4% | 100% |
| Wrong refusals | 3.0% | 0.7% |
| Unsupported claims | 0.0% | 0.0% |

### In an interview

"After the first run I read every failure and grouped them by cause: an over-cautious scope check,
the model renaming precise entities, and shortened lists. I fixed each with a general rule and re-ran
everything, which also caught one regression. Wrong refusals went from 3% to 0.7%. I'm careful to say
the overall exact-match gain is within the uncertainty of 147 questions; the targeted fixes are what
clearly held."

---

# Part 8: Operations

Running a 9B model, a graph database and a web app on one 16 GB laptop taught me that infrastructure
settings can matter as much as code.

---

## D47. Cap Docker Desktop's memory at 4 GB

### What is it?

On a Mac, Docker can't run Linux containers directly. **Docker Desktop** runs a small Linux **virtual
machine** (VM) and runs containers inside it. That VM is given a fixed amount of memory, which it
holds onto. By default it took 8 GB, half the laptop.

Apple Silicon Macs have **unified memory**: the CPU and GPU share the same 16 GB. When programs need
more than is available, macOS moves less-used memory to the SSD (**swap**). Reading it back from the
SSD is far slower than from RAM.

### How did it show up?

- Swap reached about **17 GB**.
- The model kept waiting for its weights to come back from disk: reading prompts slowed to ~80 tokens
  per second (it reached 3,291 tokens per second when nothing had to be reloaded), and some questions
  took over 100 seconds.
- Neo4j inside the VM only used about 2 GB of its 8 GB.

### The decision and the reasoning

**Decision:** set Docker Desktop's memory limit to 4 GB.

**Reasoning:**

1. **Neo4j needs ~2 GB** (1 GB heap + 1 GB page cache + overhead); 4 GB is comfortable.
2. **The freed 4 GB went to the model and the OS,** reducing swapping.
3. **Measure before optimizing:** checking `sysctl vm.swapusage` and tokens per second showed memory,
   not compute, was the bottleneck.

### Trade-offs

- Less room for other containers (none needed here).

---

## D48. Stop everything when not in use

### What is it?

The model (~6 GB), Neo4j (~2 GB in the Docker VM), SapBERT in the API process (~0.5 GB) and the browser
all share 16 GB with everything else on the laptop.

### The decision and the reasoning

**Decision:** stop the API and UI, `docker compose stop`, and `ollama stop qwen3.5:9b` when not testing.

**Reasoning:** the laptop is also for everything else, and leaving the stack running made the whole
machine slow. Nothing is lost when stopping: the graph lives in a Docker volume and the model on disk.

---

## D49. Not hosted publicly yet

### What is it?

Hosting means running the app on a server so anyone can open a link. The hard part is the model: it
needs a GPU to be usably fast.

### The decision and the reasoning

**Decision:** local only for now.

**Reasoning:**

1. **Free GPUs are scarce.** Azure for Students credits don't include GPU machines. Free notebook GPUs
   (Kaggle, Colab) can't host a live app.
2. **Free API tiers send data off the machine,** which changes the privacy story.
3. **Workable free options exist** if a public demo is needed: a Hugging Face Docker Space (free CPU)
   for Neo4j, the API and the UI, with the model on a serverless GPU (Modal's monthly credit) or a free
   hosted model API.

---

# Summary of trade-offs

| I chose | Instead of | What I gained | What I gave up |
|---|---|---|---|
| Grounding in retrieved facts (D1) | Asking the model | 0% vs 39% unsupported claims; abstention; provenance | Coverage limited to the graph |
| Knowledge graph (D2) | Vector RAG over documents | Exact relationships, counts, multi-hop, follow-ups | Free-text questions outside the schema |
| PrimeKG, reshaped (D4–D6) | Raw data or my own graph | Curated facts; a schema the model reads easily; checkable directions | A 2023 snapshot; conversion code |
| Neo4j with built-in search (D8–D11) | SQL or several databases | Readable queries; one system for queries and search | No read-only users in Community edition |
| Local Qwen3.5-9B, 4-bit (D3, D13–D15) | Hosted API model | Privacy, zero cost, reproducibility | ~37 s per question |
| SapBERT + LLM suggestions (D18, D23, D24) | General embeddings only | 2× linking accuracy on lay terms | An extra model; brand names need the LLM |
| Text-to-Cypher with layered checks and retries (D20, D26–D28) | Templates | Open-ended questions with safety and self-correction | Valid-but-wrong queries are still possible |
| Fallback and fixed "not found" (D29) | Always answering | Honest abstention | Answers can mention lookalike entities |
| LangGraph state machine (D33) | Plain code or an agent | Explicit loops, streaming, tracing | A framework to learn |
| Exact-gold evaluation (D40–D46) | LLM-as-judge | Deterministic, unbiased scores; real error analysis | Measures faithfulness to PrimeKG, not medical truth |

---

# Decisions I'd revisit

Each of these addresses a weakness found in testing or evaluation.

1. **A claim checker after the answer step.** Automatically check every drug, gene and symptom named in
   the answer against the retrieved rows, and remove or flag anything unsupported. This turns "the
   model is told to stay grounded" into "the system enforces it".
2. **Carry entities through follow-ups in code.** Pass the previous answer's entities directly into the
   next query, instead of relying only on rewriting, to fix "which of those" over many items.
3. **"Did you mean?" for weak matches.** When linking is only approximate, ask the user rather than
   answering about a lookalike.
4. **Skip or merge the extraction call** when the question already contains exact node names; it's the
   slowest step (~20 s).
5. **A small fine-tuned Text-to-Cypher model** (trained on Neo4j's public dataset plus this schema) for
   faster, more accurate queries.
6. **A vector index over drug and disease descriptions** for explanatory questions (hybrid retrieval).
7. **A bigger, more natural test set.** Questions written by people, around a thousand of them, to
   detect small improvements with statistical confidence.
