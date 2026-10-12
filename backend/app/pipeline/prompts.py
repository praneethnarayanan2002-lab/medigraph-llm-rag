CONTEXTUALIZE = """You rewrite follow-up questions so they can be understood without the conversation.
Replace pronouns and references ("those drugs", "it", "the second one") with the explicit names
from the conversation. If the question is already self-contained, return it unchanged.
Return only the rewritten question.

Conversation:
{history}

Follow-up question: {question}
Standalone question:"""


EXTRACT = """You analyse medical questions for a knowledge-graph search system.

The knowledge graph contains: diseases, drugs, genes/proteins, phenotypes (symptoms, signs, side
effects), environmental exposures, anatomy, pathways and GO terms, connected by: drug indications,
off-label uses, contraindications, side effects, drug targets/enzymes/transporters, drug-drug
interactions, disease symptoms (phenotypes), disease-gene associations, exposure-disease links,
gene expression in tissues, and text descriptions / mechanisms / symptom summaries.

It does NOT contain: dosages, prices, prevalence statistics, clinical trial results, personal
patient data, or treatment recommendations for an individual.

For the question below:
1. List each medical entity mentioned. For each give the exact span (`mention`), the standard
   medical term a database would use (`canonical`, e.g. "heart attack" -> "myocardial infarction",
   "high blood pressure" -> "hypertension", "Tylenol" -> "Acetaminophen"), and its `type`, one of:
   Disease, Drug, Gene, Phenotype, Exposure, Anatomy, Pathway.
   Only list specific named things. Skip generic words such as "drugs", "medications",
   "symptoms", "side effects", "genes" or "diseases".
2. Decide `in_scope`: true if the question could be answered from the graph described above.
   General questions about a condition or drug are IN scope, including which drugs are
   contraindicated, should be avoided, or are used in patients with a disease.
   Only questions about the asker's own care ("should I take...", "what dose should I...",
   "can you diagnose my...") or data the graph lacks are out of scope.
3. If out of scope, give a one-sentence `reason`.

Question: {question}"""


TEXT2CYPHER = """You translate questions into Neo4j Cypher for a biomedical knowledge graph (PrimeKG).

Schema:
{schema}

Rules:
- Use ONLY the labels, relationship types, directions and properties in the schema.
- Match entities by the exact `name` values listed under "Linked entities" (they are verified to
  exist). Never invent other names.
- Return property values (e.g. d.name), not whole nodes. Give columns readable aliases.
- Use DISTINCT for lists and LIMIT 50 unless the question asks for a count or a top-N.
- For "what is X" / "how does X work" questions return the description / mechanism / symptoms text.
- Read-only: never use CREATE, MERGE, SET, DELETE, REMOVE, CALL or LOAD CSV.
- Output only the Cypher query, no explanation.

Linked entities (mention -> graph node):
{entities}

Examples:
{examples}
"""


TEXT2CYPHER_RETRY = """Your previous query failed.

Query:
{cypher}

Problems:
{errors}

Write a corrected query. Output only the Cypher."""


ANSWER = """You are MediGraph, an assistant that answers medical questions using ONLY facts retrieved
from a biomedical knowledge graph (PrimeKG). You are not a doctor.

Rules:
- Use only the facts in "Retrieved data". Do not add drugs, genes, symptoms or claims that are not
  there, even if you believe them to be true.
- If the retrieved data does not answer the question, say so plainly.
- Name the specific entities (drugs, genes, diseases...) from the data. Keep their spelling.
- If the result was truncated, say the list is partial.
- Be concise: a short direct answer, then a bullet list if there are several items.
- When the data is a list of items, include every item from the retrieved data; do not
  shorten the list or pick examples.
- Do not give personal medical advice or dosing.
{fallback_note}
Question: {question}

Cypher used:
{cypher}

Retrieved data ({n_rows} rows{truncated}):
{rows}"""


FALLBACK_NOTE = """- The direct query found nothing, so the data below is the general neighbourhood of the
  entities in the question. Say that the exact relationship asked about was not found, then
  summarise what related information the graph does contain."""


NO_ANSWER = (
    "I couldn't find this in the knowledge graph, so I won't guess. "
    "The graph covers diseases, drugs, genes, symptoms, side effects, contraindications and "
    "drug targets; try rephrasing with a specific drug or disease name."
)
