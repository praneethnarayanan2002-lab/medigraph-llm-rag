"""Convert raw PrimeKG files into CSVs for `neo4j-admin database import`.

PrimeKG ships every undirected edge twice (x->y and y->x) with generic relation names.
Here we:
  * map node types to clean labels (Disease, Drug, Gene, ...),
  * map relations to readable, directed relationship types (Drug-[:INDICATED_FOR]->Disease),
  * orient each edge by a fixed label ranking and drop the mirrored duplicate,
  * attach short text descriptions to Drug and Disease nodes.

Usage: uv run python data/scripts/build_import.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[1]
RAW = DATA_DIR / "raw"
OUT = DATA_DIR / "import"

LABELS = {
    "drug": "Drug",
    "disease": "Disease",
    "exposure": "Exposure",
    "effect/phenotype": "Phenotype",
    "anatomy": "Anatomy",
    "pathway": "Pathway",
    "biological_process": "BiologicalProcess",
    "molecular_function": "MolecularFunction",
    "cellular_component": "CellularComponent",
    "gene/protein": "Gene",
}
# Edges point from the lower-ranked label to the higher-ranked one, e.g. Drug -> Disease -> Gene.
RANK = {label: i for i, label in enumerate(LABELS.values())}

DRUG_PROTEIN = {
    "target": "TARGETS",
    "enzyme": "METABOLIZED_BY",
    "transporter": "TRANSPORTED_BY",
    "carrier": "CARRIED_BY",
}
RELATIONS = {
    "indication": "INDICATED_FOR",
    "contraindication": "CONTRAINDICATED_FOR",
    "off-label use": "OFF_LABEL_USE_FOR",
    "drug_drug": "SYNERGISTIC_WITH",
    "drug_effect": "HAS_SIDE_EFFECT",
    "disease_phenotype_positive": "HAS_PHENOTYPE",
    "disease_phenotype_negative": "LACKS_PHENOTYPE",
    "disease_protein": "ASSOCIATED_WITH_GENE",
    "phenotype_protein": "ASSOCIATED_WITH_GENE",
    "protein_protein": "INTERACTS_WITH",
    "exposure_disease": "LINKED_TO",
    "exposure_protein": "INTERACTS_WITH",
    "exposure_bioprocess": "INTERACTS_WITH",
    "exposure_molfunc": "INTERACTS_WITH",
    "exposure_cellcomp": "INTERACTS_WITH",
    "bioprocess_protein": "INVOLVES_GENE",
    "molfunc_protein": "INVOLVES_GENE",
    "cellcomp_protein": "INVOLVES_GENE",
    "pathway_protein": "INVOLVES_GENE",
    "anatomy_protein_present": "EXPRESSES_GENE",
    "anatomy_protein_absent": "LACKS_EXPRESSION_OF",
    "disease_disease": "PARENT_CHILD",
    "phenotype_phenotype": "PARENT_CHILD",
    "bioprocess_bioprocess": "PARENT_CHILD",
    "molfunc_molfunc": "PARENT_CHILD",
    "cellcomp_cellcomp": "PARENT_CHILD",
    "pathway_pathway": "PARENT_CHILD",
    "anatomy_anatomy": "PARENT_CHILD",
    "exposure_exposure": "PARENT_CHILD",
}

TEXT_LIMIT = 700


def clean_text(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .astype(str)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.slice(0, TEXT_LIMIT)
    )


def first_non_empty(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    out = pd.Series("", index=df.index)
    for col in reversed(cols):
        if col in df:
            text = clean_text(df[col])
            out = text.where(text != "", out)
    return out


def build_nodes() -> pd.DataFrame:
    nodes = pd.read_csv(RAW / "nodes.tab", sep="\t", dtype={"node_id": str}, low_memory=False)
    unknown = set(nodes["node_type"]) - set(LABELS)
    if unknown:
        sys.exit(f"Unknown node types: {unknown}")
    nodes["label"] = nodes["node_type"].map(LABELS)

    drugs = pd.read_csv(RAW / "drug_features.tab", sep="\t", low_memory=False)
    drugs = drugs.drop_duplicates("node_index").set_index("node_index")
    drug_text = pd.DataFrame(
        {
            "description": first_non_empty(drugs, ["description"]),
            "mechanism": first_non_empty(drugs, ["mechanism_of_action"]),
        }
    )

    diseases = pd.read_csv(RAW / "disease_features.tab", sep="\t", low_memory=False)
    diseases = diseases.drop_duplicates("node_index").set_index("node_index")
    disease_text = pd.DataFrame(
        {
            "description": first_non_empty(
                diseases, ["mondo_definition", "umls_description", "orphanet_definition"]
            ),
            "symptoms": first_non_empty(diseases, ["mayo_symptoms", "orphanet_clinical_description"]),
        }
    )

    text = pd.concat([drug_text, disease_text])
    nodes = nodes.join(text, on="node_index")
    for col in ("description", "mechanism", "symptoms"):
        nodes[col] = nodes[col].fillna("")
    return nodes


def build_edges(nodes: pd.DataFrame) -> pd.DataFrame:
    edges = pd.read_csv(
        RAW / "edges.csv",
        dtype={"relation": "category", "display_relation": "category"},
        low_memory=False,
    )
    print(f"raw edges: {len(edges):,}")

    rel_type = edges["relation"].map(RELATIONS).astype(object)
    is_dp = edges["relation"] == "drug_protein"
    rel_type[is_dp] = edges.loc[is_dp, "display_relation"].map(DRUG_PROTEIN)
    if rel_type.isna().any():
        missing = edges.loc[rel_type.isna(), ["relation", "display_relation"]].drop_duplicates()
        sys.exit(f"Unmapped relations:\n{missing}")
    edges["type"] = rel_type

    rank = nodes.set_index("node_index")["label"].map(RANK)
    xr = edges["x_index"].map(rank)
    yr = edges["y_index"].map(rank)
    swap = (xr > yr) | ((xr == yr) & (edges["x_index"] > edges["y_index"]))
    src = edges["x_index"].where(~swap, edges["y_index"])
    dst = edges["y_index"].where(~swap, edges["x_index"])

    out = pd.DataFrame({":START_ID": src, ":END_ID": dst, ":TYPE": edges["type"]})
    out = out.drop_duplicates()
    print(f"directed, de-duplicated edges: {len(out):,}")
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    nodes = build_nodes()
    edges = build_edges(nodes)

    node_csv = pd.DataFrame(
        {
            "node_index:ID": nodes["node_index"],
            "name": clean_text(nodes["node_name"]),
            "source_id": nodes["node_id"].astype(str),
            "source": nodes["node_source"],
            "description": nodes["description"],
            "mechanism": nodes["mechanism"],
            "symptoms": nodes["symptoms"],
            ":LABEL": nodes["label"],
        }
    )
    node_csv.to_csv(OUT / "nodes.csv", index=False)
    edges.to_csv(OUT / "relationships.csv", index=False)

    print("\nnodes by label:")
    print(nodes["label"].value_counts().to_string())
    print("\nrelationships by type:")
    print(edges[":TYPE"].value_counts().to_string())


if __name__ == "__main__":
    main()
