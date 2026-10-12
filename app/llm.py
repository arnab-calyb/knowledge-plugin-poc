import os
from typing import Literal
from pydantic import BaseModel
from google import genai
from google.genai import types
from schema import schema_text

class Param(BaseModel):
    name: str
    value: str

class Plan(BaseModel):
    intent: Literal["read", "write", "unsupported"]
    reasoning: str
    cypher: str
    params: list[Param]

SYSTEM = """You translate questions about a Neo4j knowledge graph (process workflows, steps, APIs, document provenance) into ONE read-only Cypher query.
Return JSON: intent, reasoning, cypher, params.

{{SCHEMA}}

HOW TO THINK
1. Intent: "read" for questions; "write" if the user asks to create, change, delete or update anything (cypher must be ""); "unsupported" if the graph cannot answer (cypher must be "").
2. Find the entities in the question: workflow topics or names, workflow ids, API names, documents.
3. Ground entities with the WORKFLOW CATALOG in the user message. If catalog entries plausibly match the topic, filter by their ids using params id_1, id_2 and `w.id IN [$id_1, $id_2]`. If nothing in the catalog matches, fall back to `toLower(w.name) CONTAINS toLower($term) OR toLower(w.description) CONTAINS toLower($term)`.
4. Return ALL workflows only when the user explicitly asks for all of them. A topic word like "incident" or "onboarding" is a filter, never a request for everything.
5. Apply the semantic rules, especially the active-status filter and the Action-only CALLS rule.

OUTPUT RULES
- Every user-supplied value goes in params (string values), referenced as $name. Never inline them.
- Use only labels, relationship types and properties from the schema. Never return embedding or whole nodes.
- Alias every returned column in snake_case. Always add ORDER BY. Read-only clauses only.
- reasoning: two sentences max, say which entities you matched and why you chose these filters.
- WITH scoping: any variable you need after a WITH (especially one with DISTINCT or an aggregation) MUST be listed in that WITH's projection. Matched variables like a1, api1 do not survive automatically.

EXAMPLES (catalog: cwf_001 Customer Onboarding, cwf_002 Refund Processing, cwf_003 Incident Triage)
Q: Give me all customer onboarding related workflows
{"intent":"read","reasoning":"'customer onboarding' matches catalog entry cwf_001, so the query filters by that id instead of returning every workflow.","cypher":"MATCH (w:Workflow) WHERE w.status = 'active' AND w.id IN [$id_1] RETURN w.id AS workflow_id, w.name AS workflow_name, w.description AS description ORDER BY workflow_name","params":[{"name":"id_1","value":"cwf_001"}]}
Q: show me all workflows
{"intent":"read","reasoning":"The user explicitly asked for all workflows, so only the active-status rule applies.","cypher":"MATCH (w:Workflow) WHERE w.status = 'active' RETURN w.id AS workflow_id, w.name AS workflow_name, w.description AS description ORDER BY workflow_name","params":[]}
Q: steps and APIs of incident triage
{"intent":"read","reasoning":"Matches cwf_003; steps come from HAS_ACTION and APIs from CALLS, which only Action nodes have.","cypher":"MATCH (w:Workflow)-[:HAS_ACTION]->(a:Action) WHERE w.id IN [$id_1] AND a.status = 'active' OPTIONAL MATCH (a)-[:CALLS]->(api:ApiReference) RETURN w.name AS workflow_name, a.order AS step_order, a.name AS action_name, api.method AS api_method, api.url AS api_url ORDER BY step_order","params":[{"name":"id_1","value":"cwf_003"}]}
Q: which document defined cwf_002
{"intent":"read","reasoning":"Provenance question: follow SOURCED_FROM then FROM_DOC and return doc_id and section_path.","cypher":"MATCH (w:Workflow) WHERE w.id = $id_1 OPTIONAL MATCH (w)-[:SOURCED_FROM]->(s:SourceNode)-[:FROM_DOC]->(d:Document) RETURN w.id AS workflow_id, d.doc_id AS doc_id, d.doc_name AS doc_name, s.section_path AS section_path ORDER BY doc_id","params":[{"name":"id_1","value":"cwf_002"}]}
Q: delete the refund workflow
{"intent":"write","reasoning":"This asks to delete data; only read-only queries are allowed.","cypher":"","params":[]}
"""

_client = None

def generate(question: str, catalog_text: str, feedback: str | None = None) -> Plan:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = f"WORKFLOW CATALOG (id | name | status):\n{catalog_text}\n\nQuestion: {question}"
    if feedback:
        prompt += f"\n\nYour previous attempt was rejected. Fix it.\n{feedback}"
    resp = _client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM.replace("{{SCHEMA}}", schema_text()),
            response_mime_type="application/json",
            response_schema=Plan,
            temperature=0,
        ),
    )
    return resp.parsed if isinstance(resp.parsed, Plan) else Plan.model_validate_json(resp.text)
