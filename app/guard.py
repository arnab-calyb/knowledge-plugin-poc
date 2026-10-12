"""Deterministic safety layer. Never trust the LLM: every query is validated here before it touches Neo4j."""
import re
from schema import LABELS, RELS

class GuardError(Exception):
    pass

WRITE = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD|CALL|START|GRANT|DENY|REVOKE|TERMINATE)\b", re.I)
START = re.compile(r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN)\b", re.I)
TOKENS = set(LABELS) | set(RELS)
PROPS = {p for ps in LABELS.values() for p in ps} - {"embedding"}

def _strip(q: str) -> str:
    q = re.sub(r"/\*.*?\*/|//[^\n]*", " ", q, flags=re.S)
    return re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", q)

def validate(cypher: str, params: dict):
    q = cypher.strip().rstrip(";").strip()
    if not q:
        raise GuardError("empty query")
    s = _strip(q)
    if ";" in s:
        raise GuardError("multiple statements are not allowed")
    if not START.match(s):
        raise GuardError("query must start with MATCH, OPTIONAL MATCH, WITH, UNWIND or RETURN")
    m = WRITE.search(s)
    if m:
        raise GuardError(f"write or unsafe clause blocked: {m.group(1).upper()}")
    if re.search(r"\b(apoc|dbms|db)\.", s, re.I):
        raise GuardError("procedure and function namespaces are not allowed")
    flat = re.sub(r"\{[^{}]*\}", " ", s)
    for t in re.findall(r"[:|]\s*`?([A-Za-z_]\w*)", flat):
        if t not in TOKENS:
            raise GuardError(f"unknown label or relationship type '{t}'")
    for p in re.findall(r"\b[A-Za-z_]\w*\.([A-Za-z_]\w*)", s):
        if p not in PROPS:
            raise GuardError(f"unknown or disallowed property '{p}'")
    used = set(re.findall(r"\$(\w+)", s))
    missing = used - set(params)
    if missing:
        raise GuardError(f"parameters used but not bound: {sorted(missing)}")
    lim = re.search(r"\bLIMIT\s+(\d+)", s, re.I)
    if not lim:
        q += "\nLIMIT 100"
    elif int(lim.group(1)) > 500:
        q = re.sub(r"(?i)\bLIMIT\s+\d+", "LIMIT 500", q)
    return q, {k: v for k, v in params.items() if k in used}
