"""Static checks on LLM-written Cypher, run before anything touches the database.

Defence in depth for read-only access:
  1. `check_read_only`  - reject write/admin clauses in the text (this module)
  2. `GraphDB.explain`  - Neo4j plans the query and reports its type; anything but 'r' is rejected
  3. `GraphDB.read`     - the query runs in a READ access-mode transaction, so the server refuses writes

`check_schema` catches the most common model mistakes (made-up labels, relationship types,
properties, or reversed edges) and returns messages precise enough to feed back for a retry.
"""

from __future__ import annotations

import difflib
import re

from app.graph.schema import ALL_PROPERTIES, NODE_LABELS, REL_TYPES, RELATIONSHIPS

WRITE_CLAUSES = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH|GRANT|REVOKE|DENY)\b"
    r"|\bCALL\s*\{|\bCALL\s+(?!db\.index\.(fulltext|vector)\.query)[\w.]+",
    re.IGNORECASE,
)
STRING_LITERAL = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`[^`]*`")
CODE_FENCE = re.compile(r"```(?:cypher|sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
NAMESPACES = {"db", "apoc", "gds", "date", "datetime", "duration", "point", "time", "localtime"}
REL_DIRECTIONS = {(src, rel, dst) for src, rel, dst, _ in RELATIONSHIPS}


def extract_cypher(text: str) -> str:
    """Pull the query out of a model reply (code fences, 'Cypher:' prefixes, trailing ';')."""
    match = CODE_FENCE.search(text)
    query = match.group(1) if match else text
    query = re.sub(r"^\s*cypher\s*:?\s*", "", query.strip(), flags=re.IGNORECASE)
    return query.strip().rstrip(";").strip()


def _strip_strings(cypher: str) -> str:
    return STRING_LITERAL.sub("''", cypher)


def check_read_only(cypher: str) -> list[str]:
    errors = []
    code = _strip_strings(cypher)
    if ";" in code:
        errors.append("Only a single statement is allowed (remove ';').")
    match = WRITE_CLAUSES.search(code)
    if match:
        errors.append(f"Only read queries are allowed; found '{match.group(0).strip()}'.")
    return errors


def _suggest(word: str, options: set[str] | list[str]) -> str:
    close = difflib.get_close_matches(word, list(options), n=1, cutoff=0.5)
    return f" Did you mean '{close[0]}'?" if close else ""


def check_schema(cypher: str) -> list[str]:
    errors: list[str] = []
    code = _strip_strings(cypher)

    # Property names: var.prop, {.prop} projections, and {prop: ...} pattern maps.
    props = {
        p for var, p in re.findall(r"(?<![\w.$])([A-Za-z_]\w*)\.([A-Za-z_]\w*)", code)
        if var.lower() not in NAMESPACES
    }
    props |= set(re.findall(r"[{,]\s*\.([A-Za-z_]\w*)", code))
    for body in re.findall(r"\(\s*\w*\s*(?::\s*\w+\s*)*\{([^{}]*)\}", code):
        props |= set(re.findall(r"([A-Za-z_]\w*)\s*:", body))
    for p in sorted(props - ALL_PROPERTIES):
        errors.append(f"Unknown property '{p}'.{_suggest(p, ALL_PROPERTIES)}")

    # Labels and relationship types: every ':Name' token outside map literals.
    no_maps = code
    while re.search(r"\{[^{}]*\}", no_maps):
        no_maps = re.sub(r"\{[^{}]*\}", " ", no_maps)
    known = set(NODE_LABELS) | REL_TYPES
    names: set[str] = set()
    for first, rest in re.findall(r":\s*!?\s*([A-Za-z_]\w*)((?:\s*[|&]\s*:?\s*!?\s*[A-Za-z_]\w*)*)", no_maps):
        names.add(first)
        names |= set(re.findall(r"[A-Za-z_]\w*", rest))
    for name in sorted(names):
        if name not in known and not name.islower():
            errors.append(f"Unknown label or relationship type '{name}'.{_suggest(name, known)}")

    # Edge direction, when both endpoint labels are written in the pattern.
    node = r"\(\s*\w*\s*:\s*(\w+)[^()]*\)"
    rel = r"(<?-)\s*\[\s*\w*\s*:\s*(\w+)[^\]]*\]\s*(->?)"
    for left, arrow_l, rtype, arrow_r, right in re.findall(node + r"\s*" + rel + r"\s*" + node, no_maps):
        if rtype not in REL_TYPES or left not in NODE_LABELS or right not in NODE_LABELS:
            continue
        if arrow_r == "->":
            src, dst = left, right
        elif arrow_l == "<-":
            src, dst = right, left
        else:  # undirected pattern: either orientation is fine
            if (left, rtype, right) in REL_DIRECTIONS or (right, rtype, left) in REL_DIRECTIONS:
                continue
            src, dst = left, right
        if (src, rtype, dst) not in REL_DIRECTIONS:
            valid = [f"(:{s})-[:{r}]->(:{d})" for s, r, d in REL_DIRECTIONS if r == rtype]
            errors.append(
                f"(:{src})-[:{rtype}]->(:{dst}) does not exist. Valid: {', '.join(sorted(valid))}."
            )
    return errors


def name_literals(cypher: str) -> list[tuple[str | None, str]]:
    """(label or None, name) for each node name written as a literal in the query:
    `(:Drug {name: 'X'})`, `d.name = 'X'` and `d.name IN ['X', 'Y']` (label resolved from the
    variable's pattern when possible)."""
    var_labels = dict(re.findall(r"\(\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)", cypher))
    found: list[tuple[str | None, str]] = []
    for label, name in re.findall(
        r"\(\s*\w*\s*:\s*([A-Za-z_]\w*)\s*\{[^{}]*?\bname\s*:\s*(?:'([^']*)'|\"[^\"]*\")", cypher
    ):
        found.append((label, name))
    for var, name in re.findall(r"\b([A-Za-z_]\w*)\.name\s*=\s*'([^']*)'", cypher):
        found.append((var_labels.get(var), name))
    for var, body in re.findall(r"\b([A-Za-z_]\w*)\.name\s+IN\s*\[([^\]]*)\]", cypher):
        found += [(var_labels.get(var), n) for n in re.findall(r"'([^']*)'", body)]
    return list(dict.fromkeys(found))


def validate_static(cypher: str) -> list[str]:
    if not cypher.strip():
        return ["The model returned an empty query."]
    if not re.search(r"\bRETURN\b", cypher, re.IGNORECASE):
        return ["The query must end with a RETURN clause."]
    return check_read_only(cypher) or check_schema(cypher)
