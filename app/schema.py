LABELS = {
    "Document": ["doc_id", "platform_key", "doc_name", "doc_type", "file_hash", "indexed_at"],
    "Workflow": ["doc_id", "section_path", "id", "embedding", "name", "description", "status", "content_hash"],
    "Action": ["doc_id", "section_path", "id", "name", "description", "order", "status", "content_hash"],
    "Decision": ["doc_id", "section_path", "id", "name", "description", "order", "status", "content_hash"],
    "Exception": ["doc_id", "section_path", "id", "name", "description", "order", "status", "content_hash"],
    "ApiReference": ["doc_id", "section_path", "key", "platform_key", "name", "method", "url", "is_graphql",
                     "description", "payload", "payload_status", "payload_schema", "response", "behavioural_notes",
                     "param_names", "required_params", "path_params", "base_url", "auth_scopes", "deprecated",
                     "extraction", "content_key", "source_type"],
    "SourceNode": ["doc_id", "section_path", "id", "status", "section_hash", "ingested_at", "updated_at", "frozen"],
    "PlatformConfig": [],
}
RELS = {
    "CALLS": "(:Action)-[:CALLS]->(:ApiReference)",
    "HAS_ACTION": "(:Workflow)-[:HAS_ACTION]->(:Action)",
    "HAS_DECISION": "(:Workflow)-[:HAS_DECISION]->(:Decision)",
    "NEXT": "(:Action|Decision|Exception)-[:NEXT]->(:Action|Decision|Exception)",
    "FROM_DOC": "(:SourceNode)-[:FROM_DOC]->(:Document)",
    "SOURCED_FROM": "(node)-[:SOURCED_FROM]->(:SourceNode)",
}
RULES = [
    "Steps are Action, Decision or Exception nodes.",
    "ONLY Action nodes connect to ApiReference via CALLS. Decision and Exception never call APIs.",
    "Sequential execution between steps uses the NEXT edge.",
    "Provenance: (node)-[:SOURCED_FROM]->(:SourceNode)-[:FROM_DOC]->(:Document). Return doc_id and section_path for citations.",
    "Filter status = 'active' for workflows and steps unless historical or inactive items are explicitly requested.",
]

# What each node type IS and WHY it is modeled this way. Structure alone does not
# tell the model when a label is the right entry point; this does.
DESCRIPTIONS = {
    "Document": "A source file (runbook, SOP, API spec) the graph was extracted from. Root of provenance. Use only when the user asks which document something came from; never as a starting point for process questions.",
    "Workflow": "A named business process (e.g. Customer Onboarding). The standard entry point for most questions: discover by topic, then traverse into steps.",
    "Action": "An executable step in a workflow. The ONLY step type that may call APIs. Use for 'what does the workflow do', 'which APIs are called', 'what happens in step N'.",
    "Decision": "A branch point in a workflow (a business rule that splits the path). Never calls APIs. Use for 'what conditions exist', 'when does it go down path X'.",
    "Exception": "An error or edge-case path in a workflow. Never calls APIs. Use for 'what happens when X fails', 'failure paths'.",
    "ApiReference": "An HTTP or GraphQL endpoint an Action invokes. Holds method, URL, payload, auth and deprecation info. Use for API details or reverse questions ('which workflows call this API').",
    "SourceNode": "Provenance anchor linking an extracted node back to the exact section of a Document. Use only for citations, never for process questions.",
    "PlatformConfig": "Platform-level configuration nodes. No useful properties for Q&A; ignore unless the user explicitly asks about platform config.",
}

REL_DESCRIPTIONS = {
    "CALLS": "Action -> ApiReference. The only way any step reaches an API. Reverse it (api<-[:CALLS]-action) to answer 'which workflows use this endpoint'.",
    "HAS_ACTION": "Workflow -> its executable steps. First hop for 'what does this workflow do'.",
    "HAS_DECISION": "Workflow -> its branch points. Use alongside HAS_ACTION when the user asks about conditions or logic, not just execution.",
    "NEXT": "Execution order between consecutive steps (Action|Decision|Exception). Follow the chain for sequence questions; also ORDER BY the step's order property.",
    "FROM_DOC": "SourceNode -> Document. Second hop of the provenance chain.",
    "SOURCED_FROM": "Any extracted node -> its SourceNode. First hop of the provenance chain; always continue to FROM_DOC and return doc_id/section_path for citations.",
}

# The questions this graph is DESIGNED to answer, each with its traversal pattern.
# This is the reasoning behind the schema: if a question does not map to one of
# these shapes, treat it as unsupported rather than improvising.
USE_CASES = [
    {"question_type": "Discover workflows by topic (onboarding, refunds, incidents)",
     "pattern": "(w:Workflow) filtered by catalog id when one matches, else toLower(w.name/description) CONTAINS toLower($term), status='active'."},
    {"question_type": "List the steps of a workflow",
     "pattern": "(w:Workflow)-[:HAS_ACTION]->(a:Action), add OPTIONAL HAS_DECISION if the user cares about branches; ORDER BY a.order."},
    {"question_type": "Which APIs does a workflow or step call",
     "pattern": "(w)-[:HAS_ACTION]->(a:Action)-[:CALLS]->(api:ApiReference). Never expect CALLS from Decision or Exception."},
    {"question_type": "API details (method, URL, payload, auth, deprecation)",
     "pattern": "Match the ApiReference by name/URL fragment and return its scalar properties. Do not return payload_schema or whole nodes unless asked."},
    {"question_type": "Which workflows use a given API (reverse lookup)",
     "pattern": "(api:ApiReference)<-[:CALLS]-(a:Action)<-[:HAS_ACTION]-(w:Workflow)."},
    {"question_type": "Execution order / sequence of steps",
     "pattern": "Walk NEXT between steps of one workflow, or rely on the order property with ORDER BY. Both are valid; prefer order property for flat lists."},
    {"question_type": "Citations: where did this workflow/step/API come from",
     "pattern": "(node)-[:SOURCED_FROM]->(s:SourceNode)-[:FROM_DOC]->(d:Document); return d.doc_id, d.doc_name, s.section_path."},
    {"question_type": "Failure paths and exception handling",
     "pattern": "(w)-[:HAS_ACTION]->(a)-[:NEXT]->(e:Exception) (or Exception reached via NEXT from any step); describe e.name/e.description."},
]

# Question categories the graph structurally cannot answer. If the user asks
# about these, return intent="unsupported" with a short reason -- do not guess.
OUT_OF_SCOPE = [
    "Runtime data: logs, telemetry, run history, who executed what, timestamps of executions.",
    "Performance and reliability: durations, SLAs, success rates, bottlenecks, throughput.",
    "Ownership and org: team names, owners, assignees, escalation contacts (not stored).",
    "Costs, licensing, infrastructure sizing.",
    "Full document text: only extracted nodes are stored; you cannot quote or summarise a whole document.",
    "Vector/semantic search over embeddings: the embedding property exists but must never be returned or used as a search mechanism here.",
    "Any write, create, update or delete operation.",
]

def schema_text() -> str:
    nodes = "\n".join(f"- {l}: {', '.join(p) or '(no properties)'}" for l, p in LABELS.items())
    node_desc = "\n".join(f"- {l}: {d}" for l, d in DESCRIPTIONS.items())
    rels = "\n".join(f"- {r}: {p}" for r, p in RELS.items())
    rel_desc = "\n".join(f"- {r}: {d}" for r, d in REL_DESCRIPTIONS.items())
    rules = "\n".join(f"{i+1}. {r}" for i, r in enumerate(RULES))
    uses = "\n".join(f"- {u['question_type']}\n  pattern: {u['pattern']}" for u in USE_CASES)
    oos = "\n".join(f"{i+1}. {r}" for i, r in enumerate(OUT_OF_SCOPE))
    return (
        f"NODES\n{nodes}\n\nNODE MEANING (what each type is for)\n{node_desc}\n\n"
        f"RELATIONSHIPS\n{rels}\n\nRELATIONSHIP MEANING\n{rel_desc}\n\n"
        f"SEMANTIC RULES\n{rules}\n\n"
        f"USE CASES this graph is designed to answer (map the question to one of these; if none fits, use intent='unsupported')\n{uses}\n\n"
        f"OUT OF SCOPE (always intent='unsupported')\n{oos}"
    )
