import os, re, time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import db, guard, llm
from schema import LABELS, RELS, RULES

app = FastAPI(title="NL2Cypher")
SAMPLE_CATALOG = [
    {"id": "cwf_001", "name": "Customer Onboarding", "status": "active"},
    {"id": "cwf_002", "name": "Refund Processing", "status": "active"},
    {"id": "cwf_003", "name": "Incident Triage", "status": "active"},
    {"id": "cwf_004", "name": "Legacy Invoice Sync", "status": "inactive"},
]
IMPERATIVE = re.compile(r"^\s*(please\s+)?(delete|remove|drop|update|set|change|create|insert|modify|rename|merge|activate|deactivate)\b", re.I)
CYPHER_WRITE = re.compile(r"\)\s*(SET|DELETE|REMOVE)\b|\b(CREATE|MERGE|DETACH\s+DELETE|DROP)\s*[\(\w]", re.I)

class Ask(BaseModel):
    question: str

@app.get("/api/health")
def health():
    return {"gemini": bool(os.getenv("GOOGLE_API_KEY")), "neo4j": db.enabled(), "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash")}

@app.get("/api/schema")
def schema():
    return {"labels": LABELS, "relationships": RELS, "rules": RULES}

def blocked(trace, layer, detail):
    trace.append({"stage": layer, "ok": False, "detail": detail})
    return {"status": "blocked", "layer": layer, "detail": detail, "trace": trace}

@app.post("/api/query")
def query(body: Ask):
    q = body.question.strip()
    if not q or len(q) > 500:
        raise HTTPException(400, "Question must be 1-500 characters.")
    if not os.getenv("GOOGLE_API_KEY"):
        raise HTTPException(500, "GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key.")
    trace, t0 = [], time.time()
    if IMPERATIVE.search(q) or CYPHER_WRITE.search(q):
        return blocked(trace, "Layer 1: input filter", "The request looks like a write operation.")
    trace.append({"stage": "Layer 1: input filter", "ok": True, "detail": "No write intent detected in the question."})

    live = db.enabled()
    cat = db.catalog() if live else SAMPLE_CATALOG
    cat_text = "\n".join(f"{c['id']} | {c['name']} | {c['status']}" for c in cat) or "(empty)"
    trace.append({"stage": "Entity catalog", "ok": True, "detail": f"{len(cat)} workflows loaded for grounding ({'live Neo4j' if live else 'sample data, dry-run'})."})

    feedback = None
    for attempt in range(1, 4):
        plan = llm.generate(q, cat_text, feedback)
        trace.append({"stage": f"Layer 2: Gemini (attempt {attempt})", "ok": plan.intent == "read", "detail": f"intent={plan.intent}. {plan.reasoning}"})
        if plan.intent == "write":
            return blocked(trace, "Layer 2: Gemini intent check", plan.reasoning)
        if plan.intent == "unsupported":
            return {"status": "unsupported", "reasoning": plan.reasoning, "trace": trace}
        try:
            cypher, params = guard.validate(plan.cypher, {p.name: p.value for p in plan.params})
        except guard.GuardError as e:
            trace.append({"stage": "Layer 3: Cypher validator", "ok": False, "detail": str(e)})
            feedback = f"Previous cypher: {plan.cypher}\nValidator error: {e}"
            continue
        trace.append({"stage": "Layer 3: Cypher validator", "ok": True, "detail": "Read-only, schema-compliant, parameters bound, LIMIT enforced."})
        base = {"status": "ok", "cypher": cypher, "params": params, "reasoning": plan.reasoning, "trace": trace}
        if not live:
            return {**base, "mode": "dry-run", "rows": [], "columns": [], "ms": int((time.time() - t0) * 1000)}
        try:
            t1 = time.time()
            rows = db.run_read(cypher, params)
            trace.append({"stage": "Layer 4: read-only Neo4j session", "ok": True, "detail": f"{len(rows)} rows returned."})
            return {**base, "mode": "live", "rows": rows, "columns": list(rows[0].keys()) if rows else [], "ms": int((time.time() - t1) * 1000)}
        except Exception as e:
            trace.append({"stage": "Layer 4: read-only Neo4j session", "ok": False, "detail": f"cypher: {cypher}\nerror: {str(e)[:300]}"})
            feedback = f"Previous cypher: {cypher}\nNeo4j error: {str(e)[:300]}"
    return {"status": "failed", "reasoning": "Could not produce a valid query after 3 attempts.", "trace": trace}

app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "static", html=True), name="static")
