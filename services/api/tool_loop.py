"""A bounded tool loop for the answer step: the model can search and read before it answers.

**Why this exists.** The single-shot path hands the model five passages and asks it to answer from them. On
2026-10-18 the suite showed where that breaks: the right document was retrieved 33 of 37 times, but the chunk
holding the exact value only 11 of 19 times, and 18 of the 32 failing question-runs had the value in context and
still came back refused or citation-only. The model could not ask for more. A search product whose model cannot
search is answering from what it was handed rather than finding the answer.

**What is bounded here, and why every bound is deliberate.** Each step is a model call and a tool call, and both
cost money and time. So: a step cap, a tool-call cap, a wall-clock deadline that comes from the request budget the
API already enforces, and a per-result character cap. A loop that runs out returns `(None, reason)` and the caller
falls back to the existing single-shot answer with the passages it already has, which is why enabling this cannot
be worse than not enabling it.

**The evidence list is the citation namespace.** Every chunk a tool returns is numbered and kept, and the final
answer cites those numbers. The verifier already checks an answer against a passage list; this module builds that
list from what the model actually read, so a citation cannot point at a passage the model never saw.

**Access is applied per call, not once.** Every search passes the session permit to the retriever and every read
filters `chunk_index` by the permitted roots. A tool is a second path to content, and a second path is where a
missing filter hides.
"""
from __future__ import annotations

import json
import os
import time

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the client's documents. Use different wording when the first results do not answer the "
                "question, and search again for a second document when comparing two things."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "the search query, in the client's words"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_passages",
            "description": (
                "Read the document around a search result: the rest of a table, the neighbouring rows, or the "
                "page after a hit. Use it when a result names the right table but the needed value is not in "
                "the excerpt."),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "the document id, exactly as a search returned it"},
                    "page_start": {"type": "integer", "description": "first page to read"},
                    "page_end": {"type": "integer", "description": "last page to read"},
                    "chunk_id": {"type": "string",
                                 "description": "read from this chunk instead of a page range"},
                },
                "required": ["doc_id"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You answer questions about a client's own documents.\n"
    "You have two tools. search_documents searches the documents; read_passages opens a document around a "
    "result. Use them. If the first search does not contain the answer, search again with different words. If a "
    "result names a table but the excerpt is not enough, read that document. If the question compares two "
    "documents, search for both.\n"
    "Rules for the final answer:\n"
    "1. Put the answer in the first sentence. Quote a figure exactly as it appears in the evidence, with units.\n"
    "2. Cite the evidence number that carries the answer as [n], using the numbers shown in tool results.\n"
    "3. If the answer needs two passages combined, such as a difference or a comparison, do that and cite "
    "both.\n"
    "4. Do not describe your search. Do not reply with only a citation.\n"
    "5. If the evidence does not contain the answer, reply exactly: I could not find that in the available "
    "documents.\n"
    "You have a limited number of tool calls. When you have the answer, reply with no tool call."
)

CORRECTIVE = (
    "Your previous reply was rejected: {reason}. Answer again using the evidence already gathered, or search "
    "once more if a needed value is missing. {instruction}"
)

MAX_EVIDENCE_CHARS = 9000
SNIPPET_CHARS = 700


class ToolBox:
    """The tools, the evidence they produce, and the access rules they apply.

    Kept as one object so the loop has no ambient state: a request gets a toolbox, and the toolbox is discarded
    with the request.
    """

    def __init__(self, services, permit, role: str, audit=None, search_limit: int = 8,
                 read_limit: int = 12, max_evidence: int = 24):
        self.services = services
        self.permit = permit
        self.role = role
        self.audit = audit
        self.search_limit = search_limit
        self.read_limit = read_limit
        self.max_evidence = max_evidence
        self.roots = frozenset(getattr(permit, "roots", ()) or ())
        self.evidence: list = []
        self.index: dict[str, int] = {}

    # ------------------------------------------------------------------ evidence
    def add(self, passage) -> int:
        """Add a passage to the citation namespace and return its 1-based number.

        Deduplicated by chunk id: a model that searches twice with similar wording should not see the same
        passage cited under two numbers, because a client checking a citation would then find two entries for
        one piece of text.
        """
        try:
            key = str(getattr(passage, "chunk_id", "") or "")
        except Exception:  # noqa: BLE001
            key = ""
        if key and key in self.index:
            return self.index[key]
        if len(self.evidence) >= self.max_evidence:
            return 0
        self.evidence.append(passage)
        if key:
            self.index[key] = len(self.evidence)
        return len(self.evidence)

    def texts(self) -> list[str]:
        return [getattr(passage, "text", "") or "" for passage in self.evidence]

    def render(self, passages) -> str:
        lines = []
        for passage in passages:
            number = self.add(passage)
            if not number:
                continue
            text = " ".join((getattr(passage, "text", "") or "").split())
            lines.append(f"[{number}] {passage.doc_id} p{passage.page_start} "
                         f"kind={passage.kind}: {text[:SNIPPET_CHARS]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ tools
    def search_documents(self, query: str) -> str:
        import retriever

        result = retriever.search(self.services, query, role=self.role, limit=self.search_limit,
                                  permit=self.permit)
        rendered = self.render(result.passages)
        return ("Search results for " + json.dumps(query) + ":\n" + rendered) if rendered \
            else "No results for " + json.dumps(query) + "."

    def read_passages(self, doc_id: str, page_start: int | None = None, page_end: int | None = None,
                      chunk_id: str = "") -> str:
        if not doc_id:
            return json.dumps({"error": "doc_id is required"})
        import psycopg2

        dsn = getattr(self.services, "database_url", "")
        if not dsn:
            return json.dumps({"error": "the document store is unavailable"})
        start = int(page_start or 1)
        end = int(page_end or start)
        try:
            with psycopg2.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    if chunk_id:
                        cursor.execute(
                            "select page_start, page_end from chunk_index "
                            "where chunk_id = %s and doc_id = %s and acl = any(%s)",
                            (chunk_id, doc_id, sorted(self.roots)))
                        found = cursor.fetchone()
                        if not found:
                            return json.dumps({"error": "no such chunk in the documents this session may read"})
                        start, end = int(found[0]), int(found[1])
                    cursor.execute(
                        "select chunk_id from chunk_index "
                        "where doc_id = %s and acl = any(%s) "
                        "and page_end >= %s and page_start <= %s "
                        "order by page_start, chunk_id limit %s",
                        (doc_id, sorted(self.roots), start, end, self.read_limit))
                    ids = [row[0] for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"the document store could not be read: {type(exc).__name__}"})
        if not ids:
            return json.dumps({"error": "no passages found at that location for this document, or it is a "
                                        "data set rather than a readable document"})
        import retriever

        payloads = retriever._fetch_payloads(self.services, ids, self.roots)
        passages = [retriever.Passage.from_payload(payloads[key], None, 0.0)
                    for key in ids if key in payloads]
        rendered = self.render(passages)
        return (f"Passages from {doc_id} pages {start}-{end}:\n{rendered}") if rendered \
            else json.dumps({"error": "no readable passages at that location"})

    def execute(self, name: str, arguments: str) -> str:
        """Run one tool call. A malformed call or a failing tool is returned to the model as an error, because a
        model that sees its own mistake can correct it; the loop's caps stop a model that cannot."""
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            return json.dumps({"error": "arguments must be a JSON object"})
        started = time.monotonic()
        try:
            if name == "search_documents":
                out = self.search_documents(str(args.get("query") or ""))
            elif name == "read_passages":
                out = self.read_passages(str(args.get("doc_id") or ""), args.get("page_start"),
                                         args.get("page_end"), str(args.get("chunk_id") or ""))
            else:
                out = json.dumps({"error": f"unknown tool {name!r}; available: search_documents, "
                                           f"read_passages"})
        except Exception as exc:  # noqa: BLE001 - a tool failure must not end the request
            out = json.dumps({"error": f"the tool failed: {type(exc).__name__}"})
        if self.audit is not None:
            try:
                self.audit("tool_call", "", "", "ok" if not out.startswith('{"error"') else "error",
                           {"tool": name, "argument_chars": len(arguments or ""),
                            "results": len(self.evidence),
                            "elapsed_ms": round((time.monotonic() - started) * 1000)},
                           tool=name)
            except Exception:  # noqa: BLE001 - the audit is not allowed to break a request
                pass
        return out


def run_loop(call_model, toolbox: ToolBox, question: str, seed_passages=(),
             max_steps: int = 6, max_tool_calls: int = 10, deadline: float | None = None) -> dict:
    """Run the model until it answers, bounded by steps, tool calls and a deadline.

    `call_model(messages, tools)` returns a dict: `{"content", "tool_calls", "error"}`. `tool_calls` is a list
    of `{"id", "name", "arguments"}` in the OpenAI shape the gateway speaks, which is also what the Bedrock
    providers are translated to.

    Returns `{"answer", "reason", "evidence", "steps", "tool_calls", "corrections"}`. When `answer` is None the
    caller falls back to the single-shot path; `reason` says which bound was hit.
    """
    import answer_contract

    seeded = toolbox.render(list(seed_passages))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Question: {question}\n\n"
            + (f"Evidence already retrieved, numbered:\n{seeded}\n\n" if seeded else "")
            + "Search if this is not enough, then answer.")},
    ]
    stats = {"steps": 0, "tool_calls": 0, "corrections": 0}
    limit_error = ""

    while stats["steps"] < max_steps:
        if deadline is not None and time.monotonic() >= deadline:
            return {"answer": None, "reason": "tool_deadline", "evidence": toolbox.evidence,
                    **stats}
        stats["steps"] += 1
        reply = call_model(messages, TOOLS)
        if reply.get("error"):
            return {"answer": None, "reason": reply["error"], "evidence": toolbox.evidence, **stats}
        calls = reply.get("tool_calls") or []
        if not calls:
            content = (reply.get("content") or "").strip()
            failure = answer_contract.violation(content, toolbox.texts())
            if failure and stats["steps"] < max_steps:
                stats["corrections"] += 1
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": CORRECTIVE.format(
                    reason=failure, instruction=answer_contract.corrective_message(failure))})
                continue
            return {"answer": content, "reason": None, "evidence": toolbox.evidence, **stats}

        messages.append({
            "role": "assistant", "content": reply.get("content") or "",
            "tool_calls": [{"id": call["id"], "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]}}
                           for call in calls],
        })
        for call in calls:
            if stats["tool_calls"] >= max_tool_calls:
                return {"answer": None, "reason": "tool_budget", "evidence": toolbox.evidence, **stats}
            stats["tool_calls"] += 1
            result = toolbox.execute(call.get("name") or "", call.get("arguments") or "{}")
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "",
                             "content": result})
            if isinstance(result, str) and result.startswith('{"error"'):
                limit_error = "tool_error"
    return {"answer": None, "reason": limit_error or "tool_step_limit", "evidence": toolbox.evidence, **stats}


def enabled() -> bool:
    return os.environ.get("OI_TOOL_LOOP", "off").strip().lower() in ("on", "1", "true", "yes")


def limits() -> tuple[int, int]:
    try:
        steps = int(os.environ.get("OI_TOOL_MAX_STEPS", "6"))
    except ValueError:
        steps = 6
    try:
        calls = int(os.environ.get("OI_TOOL_MAX_CALLS", "10"))
    except ValueError:
        calls = 10
    return max(1, min(steps, 12)), max(1, min(calls, 24))
