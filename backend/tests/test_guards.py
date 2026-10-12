import pytest

from app.pipeline.guards import check_read_only, check_schema, extract_cypher, validate_static


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) DETACH DELETE n",
        "CREATE (:Drug {name: 'x'}) RETURN 1",
        "MATCH (d:Drug) SET d.name = 'x' RETURN d",
        "MERGE (d:Drug {name: 'x'}) RETURN d",
        "MATCH (d:Drug) REMOVE d.name RETURN d",
        "LOAD CSV FROM 'file:///x' AS row RETURN row",
        "CALL dbms.components() YIELD name RETURN name",
        "CALL apoc.periodic.iterate('a', 'b', {}) YIELD batches RETURN batches",
        "MATCH (d:Drug) RETURN d.name; MATCH (n) DELETE n",
        "MATCH (d:Drug) CALL { WITH d DELETE d } RETURN 1",
        "drop index foo",
    ],
)
def test_write_queries_rejected(query):
    assert check_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (d:Drug)-[:INDICATED_FOR]->(:Disease {name: 'asthma'}) RETURN d.name",
        # write keywords inside string literals are fine
        "MATCH (p:Phenotype {name: 'Set of symptoms; delete later'}) RETURN p.name",
        "CALL db.index.fulltext.queryNodes('entity_names', 'asthma') YIELD node RETURN node.name",
    ],
)
def test_read_queries_allowed(query):
    assert check_read_only(query) == []


def test_unknown_label_suggests_fix():
    errors = check_schema("MATCH (d:Medication)-[:INDICATED_FOR]->(x:Disease) RETURN d.name")
    assert any("Medication" in e for e in errors)


def test_unknown_relationship_suggests_fix():
    errors = check_schema("MATCH (d:Drug)-[:TREATS]->(x:Disease) RETURN d.name")
    assert any("TREATS" in e for e in errors)


def test_unknown_property():
    errors = check_schema("MATCH (d:Drug) RETURN d.dosage")
    assert any("dosage" in e for e in errors)
    errors = check_schema("MATCH (d:Drug {brand_name: 'x'}) RETURN d.name")
    assert any("brand_name" in e for e in errors)


def test_reversed_edge_detected():
    errors = check_schema("MATCH (x:Disease)-[:INDICATED_FOR]->(d:Drug) RETURN d.name")
    assert any("does not exist" in e for e in errors)
    # the same edge written right-to-left is correct
    assert check_schema("MATCH (x:Disease)<-[:INDICATED_FOR]-(d:Drug) RETURN d.name") == []
    # undirected patterns are accepted in either orientation
    assert check_schema("MATCH (x:Disease)-[:INDICATED_FOR]-(d:Drug) RETURN d.name") == []


def test_valid_complex_query_passes():
    query = """
    MATCH (:Disease {name: 'Alzheimer disease'})-[:ASSOCIATED_WITH_GENE]->(g:Gene)<-[:TARGETS]-(d:Drug)
    WITH g, collect(DISTINCT d.name) AS drugs
    RETURN g.name AS gene, drugs[..5] AS examples, size(drugs) AS n,
           [x IN drugs | toUpper(x)] AS upper, d {.name, .description} AS proj
    ORDER BY n DESC LIMIT 10
    """
    assert check_schema(query) == []


def test_extract_cypher_from_fenced_reply():
    reply = "Here you go:\n```cypher\nMATCH (d:Drug) RETURN d.name;\n```\nThis finds drugs."
    assert extract_cypher(reply) == "MATCH (d:Drug) RETURN d.name"
    assert extract_cypher("Cypher: MATCH (n) RETURN n") == "MATCH (n) RETURN n"


def test_validate_static_requires_return():
    assert validate_static("MATCH (d:Drug)")
    assert validate_static("")


def test_name_literals_found_with_labels():
    from app.pipeline.guards import name_literals

    query = (
        "MATCH (d:Drug)-[:HAS_SIDE_EFFECT]->(:Phenotype {name: 'Bleeding'})\n"
        "WHERE d.name IN ['Aspirin', 'Warfarin'] OR d.name = 'Heparin'\nRETURN d.name"
    )
    assert name_literals(query) == [
        ("Phenotype", "Bleeding"),
        ("Drug", "Heparin"),
        ("Drug", "Aspirin"),
        ("Drug", "Warfarin"),
    ]
