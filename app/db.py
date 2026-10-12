import os, json, time
from neo4j import GraphDatabase, Query, READ_ACCESS

_driver = None
_cache = (0.0, [])

def enabled() -> bool:
    return bool(os.getenv("NEO4J_URI"))

def _drv():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")))
    return _driver

def run_read(cypher: str, params: dict):
    # READ_ACCESS session: the database itself rejects writes even if every other layer failed.
    with _drv().session(database=os.getenv("NEO4J_DATABASE") or None, default_access_mode=READ_ACCESS) as s:
        rows = [r.data() for r in s.run(Query(cypher, timeout=10), params)]
    return json.loads(json.dumps(rows, default=str))

def catalog():
    """Workflow id/name/status list used to ground entity names in the prompt (cached 60s)."""
    global _cache
    if time.time() - _cache[0] > 60:
        rows = run_read("MATCH (w:Workflow) RETURN w.id AS id, w.name AS name, w.status AS status ORDER BY name LIMIT 300", {})
        _cache = (time.time(), rows)
    return _cache[1]
