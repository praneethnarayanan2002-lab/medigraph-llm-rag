"""The graph schema, written once by hand.

Introspecting Neo4j (apoc.meta) would list every label/type, but a hand-written schema lets us
state edge *directions* and *meaning*, which is what the model needs to write correct Cypher.
The same structures drive the schema validator in `pipeline/guards.py`.
"""

NODE_LABELS: dict[str, str] = {
    "Disease": "a disease or disorder (MONDO). Has `description` and `symptoms` text.",
    "Drug": "an approved or experimental drug (DrugBank). Has `description` and `mechanism` text.",
    "Gene": "a gene / protein (NCBI symbol, e.g. 'PPARG', 'TP53').",
    "Phenotype": "a clinical sign, symptom or drug side effect (HPO), e.g. 'Headache'.",
    "Exposure": "an environmental exposure, e.g. a chemical or pollutant.",
    "Anatomy": "an anatomical structure / tissue (UBERON).",
    "Pathway": "a biological pathway (Reactome).",
    "BiologicalProcess": "a GO biological process.",
    "MolecularFunction": "a GO molecular function.",
    "CellularComponent": "a GO cellular component.",
}

COMMON_PROPERTIES = ["node_index", "name", "source_id", "source"]
EXTRA_PROPERTIES = {
    "Disease": ["description", "symptoms"],
    "Drug": ["description", "mechanism"],
}

# (source label, relationship type, target label, meaning)
RELATIONSHIPS: list[tuple[str, str, str, str]] = [
    ("Drug", "INDICATED_FOR", "Disease", "drug is an approved treatment for the disease"),
    ("Drug", "OFF_LABEL_USE_FOR", "Disease", "drug is used off-label for the disease"),
    ("Drug", "CONTRAINDICATED_FOR", "Disease", "drug should not be used with the disease"),
    ("Drug", "HAS_SIDE_EFFECT", "Phenotype", "drug can cause this side effect"),
    ("Drug", "TARGETS", "Gene", "drug acts on this protein"),
    ("Drug", "METABOLIZED_BY", "Gene", "drug is metabolised by this enzyme"),
    ("Drug", "TRANSPORTED_BY", "Gene", "drug is moved by this transporter"),
    ("Drug", "CARRIED_BY", "Gene", "drug is bound by this carrier protein"),
    ("Drug", "SYNERGISTIC_WITH", "Drug", "two drugs interact (undirected)"),
    ("Disease", "HAS_PHENOTYPE", "Phenotype", "symptom / sign present in the disease"),
    ("Disease", "LACKS_PHENOTYPE", "Phenotype", "symptom / sign typically absent"),
    ("Disease", "ASSOCIATED_WITH_GENE", "Gene", "gene is associated with the disease"),
    ("Disease", "PARENT_CHILD", "Disease", "disease hierarchy (undirected)"),
    ("Exposure", "LINKED_TO", "Disease", "exposure is linked to the disease"),
    ("Exposure", "INTERACTS_WITH", "Gene", "exposure interacts with the gene"),
    ("Phenotype", "ASSOCIATED_WITH_GENE", "Gene", "gene is associated with the phenotype"),
    ("Phenotype", "PARENT_CHILD", "Phenotype", "phenotype hierarchy (undirected)"),
    ("Anatomy", "EXPRESSES_GENE", "Gene", "gene is expressed in the tissue"),
    ("Anatomy", "LACKS_EXPRESSION_OF", "Gene", "gene is not expressed in the tissue"),
    ("Pathway", "INVOLVES_GENE", "Gene", "gene participates in the pathway"),
    ("BiologicalProcess", "INVOLVES_GENE", "Gene", "gene participates in the process"),
    ("MolecularFunction", "INVOLVES_GENE", "Gene", "gene has this function"),
    ("CellularComponent", "INVOLVES_GENE", "Gene", "gene product is located here"),
    ("Gene", "INTERACTS_WITH", "Gene", "protein-protein interaction (undirected)"),
]

# Relationship types also present but rarely useful for clinical Q&A; still valid in queries.
EXTRA_REL_TYPES = {"PARENT_CHILD", "INTERACTS_WITH"}

REL_TYPES: set[str] = {r[1] for r in RELATIONSHIPS} | EXTRA_REL_TYPES
ALL_PROPERTIES: set[str] = set(COMMON_PROPERTIES) | {
    p for props in EXTRA_PROPERTIES.values() for p in props
}

# Labels whose names are worth embedding for entity linking (Gene symbols and GO terms are not
# phrased the way users talk, and full-text search handles them fine).
LINKABLE_LABELS = ["Disease", "Drug", "Phenotype", "Gene", "Exposure", "Anatomy", "Pathway"]
EMBEDDED_LABELS = {
    "nomic": ["Disease", "Drug", "Phenotype", "Exposure", "Anatomy"],
    "sapbert": ["Disease", "Drug", "Phenotype", "Exposure", "Anatomy", "Gene", "Pathway"],
}


def schema_prompt() -> str:
    lines = ["Node labels (every node has properties: " + ", ".join(COMMON_PROPERTIES) + "):"]
    for label, desc in NODE_LABELS.items():
        extra = EXTRA_PROPERTIES.get(label)
        suffix = f" Extra properties: {', '.join(extra)}." if extra else ""
        lines.append(f"  (:{label}) {desc}{suffix}")
    lines.append("")
    lines.append("Relationships (direction matters unless marked undirected):")
    for src, rel, dst, meaning in RELATIONSHIPS:
        lines.append(f"  (:{src})-[:{rel}]->(:{dst})  // {meaning}")
    return "\n".join(lines)
