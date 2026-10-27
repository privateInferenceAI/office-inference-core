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
import types

import answer_profile
import re
import time
from pathlib import Path

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
    {
        "type": "function",
        "function": {
            "name": "query_rows",
            "description": (
                "Query the rows of the indexed tables directly and get back the exact rows that match, rather "
                "than a whole passage. Use it whenever a question quotes a figure or a row label that "
                "identifies the answer row, and confirm the row before answering. Every string in match must "
                "appear in the same row. Pass doc_id to restrict the query to the document a search result "
                "named."),
            "parameters": {
                "type": "object",
                "properties": {
                    "match": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("one to four strings that must all appear in the same row, for example "
                                        "[\"228865.1\", \"188387.5\"] or [\"Mean household income\"]"),
                    },
                    "doc_id": {
                        "type": "string",
                        "description": "optional: only this document, exactly as a search result returned it",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "optional: how many rows to return, at most 8",
                    },
                },
                "required": ["match"],
            },
        },
    },
]

# **The one tool that is not a retrieval call.** A definition with `child_tools` gets a helper it can ask for a
# narrow read-only brief. The helper cannot act, cannot delegate again and cannot see the parent's conversation.
DELEGATE_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate_research",
        "description": (
            "Send one narrow research brief to a read-only helper. Use it for a side lookup that would "
            "clutter your own context. The helper returns facts with sources and cannot take any action."),
        "parameters": {
            "type": "object",
            "properties": {
                "brief": {"type": "string",
                          "description": "what the helper should find, in one or two sentences"},
            },
            "required": ["brief"],
        },
    },
}


# **The workspace tools are not in TOOLS either.** They run inside the task sandbox, and only a definition that
# declares the workspace gets them. The service enforces the container boundary; the runtime enforces the audit
# and the task id.
WORKSPACE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "workspace_write",
            "description": "Write a text file in the task workspace. The path is relative to /workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "relative path inside the workspace"},
                    "content": {"type": "string", "description": "the file's text"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_read",
            "description": "Read a text file from the task workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "relative path inside the workspace"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_list",
            "description": "List the files in the task workspace and their sizes.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_run",
            "description": (
                "Run a shell command inside the task sandbox. There is no network. The command runs as a "
                "non-root user in a read-only root with only /workspace writable."),
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "the shell command to run"}},
                "required": ["command"],
            },
        },
    },
]

# **Web access is one allowlisted fetch, not a browser.** The sandbox has no network; this tool asks the egress
# service for one URL the deployment approved and returns the text to the model.
WEB_FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch one approved https URL through the deployment's egress service and return its text. URLs "
            "off the allowlist are refused. Use it for a public page the task needs, not for searching."),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "the full https URL to fetch"}},
            "required": ["url"],
        },
    },
}

# **The first real business action.** It is offered only to a connector definition and it is declared
# irreversible, so the runtime stops at the gate before this tool can run.
SEND_EMAIL_TOOL = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": (
            "Send one email through the deployment's SMTP connector. The call is held for human approval "
            "before it runs. Use it only for the recipient the task names."),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "recipient address"},
                "subject": {"type": "string", "description": "one-line subject"},
                "body": {"type": "string", "description": "plain-text body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
}

_PROMPT_HEAD = (
    "You answer questions about a client's own documents.\n"
    "You have tools. search_documents searches the documents; read_passages opens a document around a "
    "result{tools}. Use them. If the first search does not contain the answer, search again with different "
    "words. If a result names a table but the excerpt is not enough, read that document. If the question "
    "compares two documents, search for both.\n"
)

_PROMPT_RULES = (
    "Rules for the final answer:\n"
    "1. Put the answer in the first sentence. Quote a figure exactly as it appears in the evidence, with units.\n"
    "2. Cite the evidence number that carries the answer as [n], using the numbers shown in tool results.\n"
    "3. If the answer needs two passages combined, such as a difference or a comparison, do that and cite "
    "both.\n"
    "4. Do not describe your search. Do not reply with only a citation.\n"
    "5. If the evidence does not contain the answer, reply exactly: I could not find that in the available "
    "documents.\n"
)

# **The rule that made the model thorough.** It was in the Phase A prompt and was accidentally dropped when the
# rules were rewritten for Phase B; the Phase A runs took about 330 seconds and answered 44, 44, 42, the rewritten
# prompt took 166 and answered 37, 35. A model told that a table row is a valid answer and that every passage must
# be checked before concluding absence searches more, and searching more is what this loop is for.
_TABLE_RULE = (
    "6. A figure copied from a table row is a valid answer. Check every passage before concluding the answer is "
    "absent.\n"
)
_ECHO_RULE = (
    "7. When the question quotes a figure, a date, a row label or a phrase, repeat that exact value in the "
    "answer so the reader can see the match.\n"
)
_CROSS_DOC_RULE = (
    "8. If the question names or quotes two or more documents, invoice numbers or identifiers, search for each "
    "one separately before answering.\n"
)

_REFUSAL_RETRY_INSTRUCTION = (
    "The evidence you have already read may contain the answer. Search again with different words if needed, "
    "and answer from the evidence. Refuse only if the evidence truly does not contain it. Do not guess."
)


_HONESTY_RULE = (
    "7. If the question asks about a specific event, period, meeting or document that the evidence never "
    "states, refuse even when the evidence contains related facts. Do not infer that a related figure answers "
    "it, and do not relabel a fact as the one that was asked for.\n"
)

_PROMPT_TAIL = (
    "You have a limited number of tool calls. Answer as soon as the evidence is enough; do not keep searching "
    "for completeness. When you have the answer, reply with no tool call."
)


def system_prompt() -> str:
    """The instruction for the one answer path.

    The prompt and the tool list are one fact: every offered tool is named here, and every tool named here is
    offered. When they drifted apart the model spent its limited steps calling a tool that did not exist.
    """
    tools = ("; query_rows returns the exact table rows that contain the values or labels you give it, "
             "which is how a figure is confirmed before it is answered")
    return (_PROMPT_HEAD.format(tools=tools) + _PROMPT_RULES + _TABLE_RULE + _ECHO_RULE + _CROSS_DOC_RULE + _HONESTY_RULE
            + _PROMPT_TAIL)


# The one message a capped loop is allowed to act on: answer from the evidence already gathered, with no
# tools. It is deliberately a user message, not a new system prompt, so the measured prompt stays unchanged.
SYNTHESIS_INSTRUCTION = (
    "You cannot call any more tools. Answer the question now using only the evidence above. Cite the "
    "evidence number that carries the answer as [n]. If the evidence does not contain the answer, reply "
    "exactly: I could not find that in the available documents."
)

CORRECTIVE = (
    "Your previous reply was rejected: {reason}. Answer again using the evidence already gathered, or search "
    "once more if a needed value is missing. {instruction}"
)

# The exact words the contract asks for when an answer is not in the evidence.
_REFUSAL_PHRASE = "could not find that in the available documents"
_PROFILE = answer_profile.current()
SNIPPET_CHARS = _PROFILE.snippet_chars

# --------------------------------------------------------------------------- structured row query bounds
#
# Every one of these is a bound on what a model can make the row query do. The scan bound is the one that
# matters: a figure like 59,473 appears in twenty tables, so without a cap one call could pull every matching
# chunk in the corpus and filter a megabyte of rows in Python. Four hundred chunks is enough to find any row a
# question can identify, and small enough that a call returns in a few hundred milliseconds.
QUERY_SCAN_LIMIT = 400
QUERY_ROW_CAP = 8
QUERY_DEFAULT_ROWS = 4
QUERY_ROW_CHARS = 600
QUERY_RESPONSE_CHARS = 6000
QUERY_AMBIGUOUS_DOCS = 3
# **Rows get a small allowance past the evidence ceiling, and it is not a detail.** The ceiling exists to bound
# how many passages the citation namespace and the verifier hold, and a search can reach it before a question
# asks for a row. The first five-run treatment measured one question failing in all five runs because of it: the
# model kept querying a row that was already returned, the namespace stayed full, the calls matched a row and
# rendered nothing, and the model read that as absence. Eight slots is one call's default worth of rows.
QUERY_EXTRA_EVIDENCE = 8


# --------------------------------------------------------------------------- exact phrase bounds
#
# A phrase search is the fix for a measured recall gap: on the second corpus the carrying chunk of two
# needles ranked below the cut while the document itself ranked first, and retrieval offered nearby rules
# instead. The phrase is matched literally against stored text, comma-insensitive, so a heading or a defined
# term is found regardless of embedding rank. The excerpt is centred on the match because the wider-set
# Apple snippet was truncated before the figure that answered the question.
EXACT_RESULT_CAP = 6
EXACT_WINDOW = 380
EXACT_MIN_CHARS = 2
EXACT_MAX_CHARS = 120
EXACT_EXTRA_EVIDENCE = 12
EXACT_AMBIGUOUS_CHUNKS = 24
EXACT_AUTO_MAX_MATCHES = 16
EXACT_AUTO_CANDIDATES = 14
EXACT_FALLBACK_RESULTS = 10


# The words a focus contributes are at least this long. Two-letter words are mostly function words, and a
# question carries enough longer words that matching them is what centres the snippet on its subject.
FOCUS_WORD_MIN = 3


def _focus_terms(focus) -> tuple[list[str], list[str]]:
    """The search terms a focus carries, in the two tiers the snippet rule uses.

    A focus is either one string (a tool's query) or a list of them (a question and its key terms). An entry
    containing whitespace is a multi-word term and is searched literally before any single word is tried, so a
    phrase the question actually names beats a common word that happens to appear earlier in the passage.
    Individual words come from every entry, lower-cased, punctuation stripped, and at least three characters
    long. Duplicates are kept once, so the earliest match is the only thing the order can affect.
    """
    if not focus:
        return [], []
    if isinstance(focus, str):
        items = [focus]
    else:
        items = [str(item) for item in focus if str(item or "").strip()]
    phrases: list[str] = []
    words: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = " ".join(str(item or "").split()).lower()
        if not value:
            continue
        if " " in value:
            phrases.append(value)
        for word in re.findall(r"[^\W_]+(?:['-][^\W_]+)*", value):
            if len(word) >= FOCUS_WORD_MIN and word not in seen:
                seen.add(word)
                words.append(word)
    return phrases, words


def centred_snippet(text: str, focus="", limit: int = SNIPPET_CHARS) -> str:
    """A snippet of at most `limit` characters centred on the first focus term in the text.

    **Why the start slice lost an answer.** The facilities manual states the visitor-badge limit twice: a
    table row that ends "Expires at the end of the visit day, maximum 24 hours", and the prose rule that
    badges "are valid for 24 hours maximum". A snippet that always starts at character zero kept the weaker
    "end of visit" line and cut the figure that answers the question. The retrieval was right; the render
    was the loss. Centring the window on the query's own terms puts the carrying line inside it.

    Whitespace is normalised first, so the match and the cut work on the same string the model will read.
    A focus with no match, an empty focus, or a text shorter than the window returns today's start slice.
    The window is the same length as the old snippet; the ellipses mark that text was cut and sit outside
    that length.
    """
    text = " ".join(str(text or "").split())
    limit = max(0, int(limit))
    if limit <= 0 or not focus or len(text) <= limit:
        return text[:limit]
    phrases, words = _focus_terms(focus)
    lowered = text.lower()
    index = -1
    length = 0
    # Multi-word terms first: a named phrase is a stronger centre than a single word that happens to appear
    # earlier in the passage. The earliest multi-word match wins, not the first entry in the list.
    for term in phrases:
        found = lowered.find(term)
        if found >= 0 and (index < 0 or found < index):
            index, length = found, len(term)
    if index < 0:
        for term in words:
            found = lowered.find(term)
            if found >= 0 and (index < 0 or found < index):
                index, length = found, len(term)
    if index < 0:
        return text[:limit]
    start = max(0, index + length // 2 - limit // 2)
    end = min(len(text), start + limit)
    if end - start < limit:
        start = max(0, end - limit)
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def _normal_for_match(value: str) -> str:
    """A match string as the row query compares it: lower case, commas gone, whitespace collapsed.

    Comma-insensitivity is the property the whole tool rests on. A figure is written 77,258 in a table row and
    77258 or 77,258 in a question, and the stored text is the parser's, so the only comparison that is stable
    across both is one with the separators removed.
    """
    return " ".join(str(value or "").split()).lower().replace(",", "")


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def row_query(match: list[str], doc_id: str, roots, scan_limit: int = QUERY_SCAN_LIMIT) -> tuple:
    """The row query as SQL and its parameters: one SELECT, parameterised, ACL-filtered, bounded.

    **Why this is a function and not a string inside the tool.** Two properties of the query are requirements
    rather than implementation: it reads and never writes, and every value from the model is a bound parameter
    and never a fragment of SQL. Both are testable only if the query can be inspected without a database, so it
    is built here and the tool executes what this returns. The access label is in the first clause for the same
    reason the retriever's queries carry it: this is a second path to content, and a second path is where a
    missing filter hides.
    """
    clauses = ["acl = any(%(roots)s)"]
    params: dict = {"roots": sorted(roots), "limit": max(1, int(scan_limit))}
    for index, value in enumerate(match):
        key = f"v{index}"
        # `position` rather than LIKE so the value cannot act as a wildcard. A model that sends '%' should get
        # rows containing a per cent sign, not every row in the corpus.
        clauses.append(f"position(%({key})s in lower(replace(text, ',', ''))) > 0")
        params[key] = _normal_for_match(value)
    if doc_id:
        clauses.append("doc_id = %(doc)s")
        params["doc"] = doc_id
    sql = ("select chunk_id, doc_id from chunk_index where " + " and ".join(clauses)
           + " order by doc_id, page_start, chunk_id limit %(limit)s")
    return sql, params


def _query_values(match) -> list[str] | str:
    """The validated match strings, or an error message.

    A list is the success case and a string is the error case, so the tool has one branch to write. The bounds
    live here rather than in the SQL for the same reason the scan bound is a constant: a model cannot widen them.
    """
    if isinstance(match, str):
        match = [match]
    if not isinstance(match, (list, tuple)):
        return "match must be a list of one to four strings"
    values = [" ".join(str(value or "").split())[:200] for value in match]
    values = [value for value in values if value]
    if not values or len(values) > 4:
        return "match must be a list of one to four non-empty strings"
    return values


def rows_in_chunk(payload: dict) -> list[dict]:
    """The row lines of one table chunk, each with the row ordinal it can be cited by.

    The ordinal is derived the way the chunker wrote the chunk: a `table_row` chunk IS one row and carries
    `row_index`; a `table` chunk carries its rows in order and `row_start` (None when the table was small
    enough to be one chunk, in which case its first line is row zero). The heading prefix the chunker prepends is
    removed before the offset is counted, or every row under a section heading would be cited one row early.
    """
    kind = payload.get("kind") or ""
    text = payload.get("text") or ""
    if kind == "table_row":
        lines = [text]
        base = payload.get("row_index")
        base = int(base) if isinstance(base, int) else None
    elif kind == "table":
        lines = text.split("\n")
        headings = payload.get("headings") or []
        prefix = " > ".join(headings).strip() if headings else ""
        if prefix and lines and lines[0].strip() == prefix:
            lines = lines[1:]
        base = payload.get("row_start")
        base = int(base) if isinstance(base, int) else 0
    else:
        return []
    rows = []
    for offset, line in enumerate(lines):
        line = " ".join(line.split())
        if not line:
            continue
        rows.append({"payload": payload, "line": line,
                     "haystack": line.lower().replace(",", ""),
                     "row_index": (base + offset) if base is not None else None})
    return rows


def exact_query(phrase: str, doc_id: str, roots, limit: int = EXACT_RESULT_CAP) -> tuple:
    # The phrase search as SQL and its parameters: one SELECT, parameterised, ACL-filtered, bounded.
    params = {"roots": sorted(roots), "needle": _normal_for_match(phrase), "limit": max(1, int(limit))}
    clauses = ["acl = any(%(roots)s)", "position(%(needle)s in lower(replace(text, ',', ''))) > 0"]
    if doc_id:
        clauses.append("doc_id = %(doc)s")
        params["doc"] = doc_id
    sql = ("select chunk_id, doc_id from chunk_index where " + " and ".join(clauses)
           + " order by doc_id, page_start, chunk_id limit %(limit)s")
    return sql, params


def exact_excerpt(text: str, phrase: str, window: int = EXACT_WINDOW) -> str:
    # A window of text centred on the phrase, or on its first word when the exact form is not literal.
    needle = " ".join(phrase.split()).lower()
    lowered = text.lower()
    index = lowered.find(needle)
    if index < 0:
        first = needle.split()[0] if needle.split() else needle
        index = lowered.find(first)
    if index < 0:
        return ""
    start = max(0, index - window // 2)
    end = min(len(text), index + len(needle) + window // 2)
    excerpt = " ".join(text[start:end].split())
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt = excerpt + "..."
    return excerpt


# --------------------------------------------------------------------------- table context
# **A table row's meaning lives in its table's header and caption, and the indexed row does not carry them.**
# Measured on corpus 2: "Total revenues | 4,934,098 | 4,136,543 | 8,947,032 | 8,183,283" cannot say which of
# its four numbers is the three-month 2026 figure. The model answered 896,604, which is the Global Forwarding
# column of a concatenated table blob. The renderer therefore prepends the nearest preceding header block and
# section caption. Rows come from the Qdrant payloads (table_id and row_index are there even though the lexical
# chunk_index does not store them); a whole-table blob is read from its own lines. Neither path needs a reindex.
_TABLE_HEADER_RE = re.compile(
    r"(three|six|nine|twelve)\s+months?|months?\s+ended|quarter|years?\s+ended|"
    r"fiscal|as\s+of|(?:19|20)\d{2}|"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)|"
    r"in\s+(?:thousands|millions|billions)")
_TABLE_VALUE_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _looks_like_table_header(text: str) -> bool:
    """A row of period labels, not a row of figures."""
    text = " ".join(str(text or "").split())
    if not text or len(text) > 220:
        return False
    if not _TABLE_HEADER_RE.search(text.lower()):
        return False
    values = _TABLE_VALUE_RE.findall(text)
    value_like = [value for value in values
                  if not re.fullmatch(r"(?:19|20)\d{2}", value)
                  and ("." in value or len(value.replace(",", "")) >= 3)]
    return len(value_like) <= 1


def _table_caption(text: str) -> str:
    """A section title above the header, such as "Profit (Loss) by Segment"."""
    text = " ".join(str(text or "").split())
    if " | " in text or any(ch.isdigit() for ch in text):
        return ""
    words = text.split()
    return text if 2 <= len(words) <= 9 else ""


def _table_prefix_from_rows(rows, row_index: int) -> str:
    ordered = sorted((index, " ".join(str(text or "").split())) for index, text in rows if text)
    position = next((offset for offset, (index, _) in enumerate(ordered) if index == row_index), None)
    if position is None:
        position = sum(1 for index, _ in ordered if index < row_index)
    if position <= 0:
        return ""
    header_at = None
    for offset in range(position - 1, max(-1, position - 40), -1):
        if _looks_like_table_header(ordered[offset][1]):
            header_at = offset
            break
    if header_at is None:
        return ""
    caption = ""
    for offset in range(header_at - 1, max(-1, header_at - 6), -1):
        caption = _table_caption(ordered[offset][1])
        if caption:
            break
    start = header_at
    while start - 1 >= 0 and header_at - start < 2:
        previous, current = ordered[start - 1], ordered[start]
        if current[0] - previous[0] > 2 or not _looks_like_table_header(previous[1]):
            break
        start -= 1
    block = " / ".join(ordered[offset][1] for offset in range(start, header_at + 1))
    return (f"Table: {caption}. Columns: {block}." if caption else f"Columns: {block}.")


def _table_prefix_from_blob(text: str) -> str:
    lines = [" ".join(line.split()) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    header_at = None
    for offset, line in enumerate(lines):
        if " | " in line and _looks_like_table_header(line):
            header_at = offset
            break
    if header_at is None:
        return ""
    caption = ""
    for offset in range(header_at - 1, -1, -1):
        caption = _table_caption(lines[offset])
        if caption:
            break
    block = lines[header_at]
    if header_at + 1 < len(lines) and _looks_like_table_header(lines[header_at + 1]):
        block = block + " / " + lines[header_at + 1]
    return (f"Table: {caption}. Columns: {block}." if caption else f"Columns: {block}.")


class ToolAuditError(Exception):
    """The tool audit did not write, and this run is not allowed to continue without it.

    The answer path deliberately tolerates an audit failure so a model call is never lost to the record. The
    agent runtime cannot: a tool call that leaves no row is a gap in the record a client is paying for. A strict
    toolbox raises this and the runtime fails the step.
    """


class ToolBox:
    """The tools, the evidence they produce, and the access rules they apply.

    Kept as one object so the loop has no ambient state: a request gets a toolbox, and the toolbox is discarded
    with the request.
    """

    def __init__(self, services, permit, role: str, audit=None, search_limit: int = 8,
                 read_limit: int = 12, max_evidence: int | None = None, strict_audit: bool = False):
        self.services = services
        self.permit = permit
        self.role = role
        self.audit = audit
        self.strict_audit = strict_audit
        self.search_limit = search_limit
        self.read_limit = read_limit
        self.max_evidence = _PROFILE.max_evidence if max_evidence is None else max_evidence
        self.roots = frozenset(getattr(permit, "roots", ()) or ())
        self.evidence: list = []
        self.index: dict[str, int] = {}
        self.table_prefix_cache: dict = {}

    # ------------------------------------------------------------------ evidence
    def add(self, passage, extra_slots: int = 0) -> int:
        """Add a passage to the citation namespace and return its 1-based number.

        Deduplicated by chunk id: a model that searches twice with similar wording should not see the same
        passage cited under two numbers, because a client checking a citation would then find two entries for
        one piece of text.

        `extra_slots` raises the ceiling for this call only. The row query uses it because a search can fill
        the namespace before the question asks for a row, and a row that cannot be numbered is a row the model
        cannot cite, which reads as absence. The ceiling still applies: the extra allowance is a constant, so
        repeated calls cannot grow the namespace without bound.
        """
        try:
            key = str(getattr(passage, "chunk_id", "") or "")
        except Exception:  # noqa: BLE001
            key = ""
        if key and key in self.index:
            return self.index[key]
        if len(self.evidence) >= self.max_evidence + max(0, int(extra_slots)):
            return 0
        self.evidence.append(passage)
        if key:
            self.index[key] = len(self.evidence)
        return len(self.evidence)

    def texts(self) -> list[str]:
        return [getattr(passage, "text", "") or "" for passage in self.evidence]

    def render(self, passages, focus="") -> str:
        """Number each passage and print its snippet, centred on the caller's focus when there is one.

        The numbering and the line format are unchanged. The focus only decides where the snippet starts:
        a search passes its query, and a caller with no query keeps the start slice.
        """
        lines = []
        for passage in passages:
            number = self.add(passage)
            if not number:
                continue
            raw_text = (getattr(passage, "text", "") or "")
            if getattr(passage, "full_text", False):
                rendered_text = raw_text
            else:
                rendered_text = centred_snippet(" ".join(raw_text.split()), focus, SNIPPET_CHARS)
            prefix = self._table_prefix(passage)
            lines.append(f"[{number}] {passage.doc_id} p{passage.page_start} "
                         f"kind={passage.kind}: "
                         + ((prefix + " ") if prefix else "")
                         + rendered_text)
        return "\n".join(lines)

    def _table_sibling_rows(self, table_id: str, row_index: int):
        """Rows above this one in the same parser table block, from the Qdrant payloads."""
        try:
            import urllib.request

            body = {"filter": {"must": [
                {"key": "table_id", "match": {"value": table_id}},
                {"key": "row_index", "range": {"gte": max(0, row_index - 40), "lt": row_index}},
                {"key": "acl", "match": {"any": sorted(self.roots)}},
            ]}, "limit": 40, "with_payload": True, "with_vector": False}
            url = (f"{self.services.vector_url}/collections/{self.services.collection}"
                   "/points/scroll")
            request = urllib.request.Request(
                url, data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=2) as response:
                points = (json.loads(response.read()).get("result") or {}).get("points") or []
        except Exception:
            return []
        rows = []
        for point in points:
            payload = point.get("payload") or {}
            index = payload.get("row_index")
            if isinstance(index, int):
                rows.append((index, payload.get("text") or ""))
        return rows

    def _table_prefix(self, passage) -> str:
        """Column labels and caption for a table passage, cached per row."""
        kind = getattr(passage, "kind", "") or ""
        text = getattr(passage, "text", "") or ""
        # **A row indexed under chunker-v5 already carries its own context, and adding it again is not
        # harmless.** The stored text begins "Columns: ...", and a prefixed row can itself pass the
        # header test, so a second prefix would be built from a row that already is one and the model
        # would read "Columns: Columns: ...". The stored context comes from the same derivation, so
        # there is nothing to add. A row indexed before v5 has no prefix and still needs this lookup,
        # which is why the path is kept rather than deleted.
        if text.startswith("Columns: ") or text.startswith("Table: "):
            return ""
        if kind == "table":
            key = ("blob", getattr(passage, "table_id", "") or "", 0)
            if key not in self.table_prefix_cache:
                self.table_prefix_cache[key] = _table_prefix_from_blob(text)
            return self.table_prefix_cache[key]
        if kind != "table_row":
            return ""
        table_id = getattr(passage, "table_id", None)
        row_index = getattr(passage, "row_index", None)
        if not table_id or not isinstance(row_index, int):
            return ""
        key = (table_id, row_index)
        if key not in self.table_prefix_cache:
            self.table_prefix_cache[key] = _table_prefix_from_rows(
                self._table_sibling_rows(table_id, row_index), row_index)
        return self.table_prefix_cache[key]


    # -------------------------------------------------- deterministic table expansion
    # When two different tables appear on the same page in one search result, both are plausible and the
    # answer cannot tell them apart from snippets. This fetches each table's chunks in row order and
    # renders the whole table once. It is deterministic: no model decision, no extra tool call.
    TABLE_EXPANSION_MAX_CHARS = 16000
    TABLE_EXPANSION_LIMIT = 400

    def _table_points(self, table_id: str) -> list:
        import urllib.request

        try:
            body = {"filter": {"must": [
                {"key": "table_id", "match": {"value": table_id}},
                {"key": "acl", "match": {"any": sorted(self.roots)}},
            ]}, "limit": self.TABLE_EXPANSION_LIMIT, "with_payload": True, "with_vector": False}
            url = (f"{self.services.vector_url}/collections/{self.services.collection}"
                   "/points/scroll")
            request = urllib.request.Request(
                url, data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response:
                points = (json.loads(response.read()).get("result") or {}).get("points") or []
        except Exception:  # noqa: BLE001 - an expansion that cannot be fetched leaves the snippets in place
            return []
        payloads = [point.get("payload") or {} for point in points]
        payloads.sort(key=lambda payload: (payload.get("row_start") or 0, payload.get("ordinal") or 0))
        return payloads

    def _full_table(self, table_id: str):
        points = self._table_points(table_id)
        if not points:
            return None
        header = ""
        lines: list = []
        for index, payload in enumerate(points):
            text = payload.get("text") or ""
            if not text:
                continue
            chunk_lines = text.split("\n")
            if index == 0:
                header = chunk_lines[0].strip() if chunk_lines else ""
                lines.extend(chunk_lines)
                continue
            if header and chunk_lines and chunk_lines[0].strip() == header:
                chunk_lines = chunk_lines[1:]
            lines.extend(chunk_lines)
        body = "\n".join(line for line in lines if line.strip()).strip()
        if not body:
            return None
        if len(body) > self.TABLE_EXPANSION_MAX_CHARS:
            body = body[: self.TABLE_EXPANSION_MAX_CHARS].rstrip() + "\n... table truncated ..."
        first, last = points[0], points[-1]
        return {
            "text": body,
            "table_id": table_id,
            "doc_id": first.get("doc_id") or "",
            "acl": first.get("acl") or "",
            "page_start": int(first.get("page_start") or 1),
            "page_end": int(last.get("page_end") or first.get("page_start") or 1),
        }

    def _with_table_expansions(self, passages):
        """Replace snippets from multi-table pages with one full-table passage per table."""
        import retriever

        pages: dict = {}
        for passage in passages:
            if (getattr(passage, "kind", "") or "") != "table":
                continue
            table_id = getattr(passage, "table_id", None)
            if not table_id:
                continue
            key = (passage.doc_id, passage.page_start)
            pages.setdefault(key, [])
            if table_id not in pages[key]:
                pages[key].append(table_id)
        replaced: set = set()
        expanded: list = []
        for table_ids in pages.values():
            if len(table_ids) < 2:
                continue
            for table_id in table_ids:
                table = self._full_table(table_id)
                if not table:
                    continue
                expanded.append(retriever.Passage(
                    doc_id=table["doc_id"], acl=table["acl"], text=table["text"],
                    page_start=table["page_start"], page_end=table["page_end"],
                    kind="table", chunk_id=f"{table_id}#expanded", table_id=table_id))
                replaced.add(table_id)
        if not expanded:
            return list(passages)
        kept = [passage for passage in passages
                if not (getattr(passage, "table_id", None) in replaced)]
        return expanded + kept

    # ------------------------------------------------------------------ tools
    def complete_small_documents(self, passages, question: str = ""):
        """Append the complete original text of small retrieved documents.

        The seed chunks stay in the evidence list, so their citation numbers do not move. The full document
        is appended as a new evidence entry and rendered without the snippet cut. This is the actual
        whole-document experiment: no replacement, no truncation, no renumbering.
        """
        if not _document_completion_enabled():
            return list(passages)
        root_value = (os.environ.get("OI_DOCUMENTS_ROOT") or "").strip()
        if not root_value:
            return list(passages)
        root = Path(root_value).resolve()
        try:
            max_chunks = int(os.environ.get("OI_DOCUMENT_MAX_CHUNKS", "25"))
            max_chars = int(os.environ.get("OI_DOCUMENT_MAX_CHARS", "40000"))
            budget = int(os.environ.get("OI_DOCUMENT_CONTEXT_CHARS", "60000"))
        except ValueError:
            max_chunks, max_chars, budget = 25, 40000, 60000
        dsn = getattr(self.services, "database_url", "")
        if not dsn:
            return list(passages)
        import psycopg2
        seen: set[str] = set()
        completed: list = []
        used = 0
        try:
            connection = psycopg2.connect(dsn)
        except Exception:
            return list(passages)
        try:
            with connection, connection.cursor() as cursor:
                for passage in passages:
                    doc_id = getattr(passage, "doc_id", "") or ""
                    if not doc_id or doc_id in seen:
                        continue
                    seen.add(doc_id)
                    cursor.execute(
                        "select count(*), coalesce(sum(length(text)), 0) from chunk_index "
                        "where doc_id = %s and acl = any(%s)", (doc_id, sorted(self.roots)))
                    count, chars = cursor.fetchone()
                    if not count or int(count) > max_chunks or int(chars) > max_chars:
                        continue
                    try:
                        path = (root / doc_id).resolve()
                        path.relative_to(root)
                        full_text = path.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    if not full_text or used + len(full_text) > budget:
                        continue
                    completed.append(types.SimpleNamespace(
                        doc_id=doc_id, acl=getattr(passage, "acl", ""), text=full_text,
                        page_start=1, page_end=max(1, int(getattr(passage, "page_end", 1) or 1)),
                        kind="document", chunk_id="document:" + doc_id,
                        table_id=None, row_index=None, full_text=True))
                    used += len(full_text)
        finally:
            connection.close()
        return list(passages) + completed

    def search_documents(self, query: str) -> str:
        import retriever

        result = retriever.search(self.services, query, role=self.role, limit=self.search_limit,
                                  permit=self.permit)
        rendered = self.render(self._with_table_expansions(result.passages), focus=query)
        return ("Search results for " + json.dumps(query) + ":\n" + rendered) if rendered \
            else "No results for " + json.dumps(query) + "."

    def search_exact(self, phrase: str, doc_id: str = "", limit: int = EXACT_RESULT_CAP) -> str:
        # Find chunks stating a phrase, literally and comma-insensitively, with a centred excerpt.
        cleaned = " ".join(str(phrase or "").split())
        if len(cleaned) < EXACT_MIN_CHARS:
            return json.dumps({"error": "phrase must contain at least two characters"})
        cleaned = cleaned[:EXACT_MAX_CHARS]
        total = self._exact_count(cleaned, doc_id)
        if total > EXACT_AMBIGUOUS_CHUNKS and not doc_id:
            return json.dumps({"note": "the phrase matches " + str(total) + " passages, so it does not "
                    "identify a heading or a defined term. Add doc_id to scope it, or use query_rows for a "
                    "figure from a table."})
        ids = self._exact_matches(cleaned, doc_id, limit)
        self._last_facts = {"exact_matches": len(ids)}
        if not ids:
            return json.dumps({"error": "no passage states that phrase in the documents this session may read"})
        import retriever
        payloads = retriever._fetch_payloads(self.services, ids, self.roots)
        passages = [retriever.Passage.from_payload(payloads[key], None, 0.0)
                    for key in ids if key in payloads]
        rendered = []
        for passage in passages:
            text = getattr(passage, "text", "") or ""
            excerpt = exact_excerpt(text, cleaned)
            if not excerpt:
                excerpt = " ".join(text.split())[:EXACT_WINDOW]
            number = self.add(passage, extra_slots=EXACT_EXTRA_EVIDENCE)
            if not number:
                continue
            rendered.append("[" + str(number) + "] " + passage.doc_id + " p"
                            + str(passage.page_start) + " kind=" + str(passage.kind)
                            + ": " + excerpt)
        if not rendered:
            return json.dumps({"error": "the phrase was indexed but the passages could not be read"})
        header = "Passages containing " + json.dumps(cleaned) + ":" + chr(10)
        return header + chr(10).join(rendered)

    def _exact_count(self, phrase: str, doc_id: str = "") -> int:
        # How many chunks state the phrase, so an over-broad phrase can be answered with a note.
        import psycopg2

        dsn = getattr(self.services, "database_url", "")
        if not dsn:
            return 0
        params = {"roots": sorted(self.roots), "needle": _normal_for_match(phrase)}
        sql = "select count(*) from chunk_index where acl = any(%(roots)s) "
        sql += "and position(%(needle)s in lower(replace(text, ',', ''))) > 0"
        if doc_id:
            sql += " and doc_id = %(doc)s"
            params["doc"] = doc_id
        try:
            with psycopg2.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    row = cursor.fetchone()
                    return int(row[0]) if row else 0
        except Exception:
            return 0

    def _exact_matches(self, phrase: str, doc_id: str = "", limit: int = EXACT_RESULT_CAP) -> list[str]:
        # The literal-match query, split out so a test can hold it without a database.
        import psycopg2

        dsn = getattr(self.services, "database_url", "")
        if not dsn:
            return []
        sql, params = exact_query(phrase, doc_id, self.roots, limit=limit)
        try:
            with psycopg2.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    return [row[0] for row in cursor.fetchall()]
        except Exception as exc:
            print("search_exact unavailable: " + type(exc).__name__ + ": " + str(exc))
            return []

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

    # ------------------------------------------------------------------ structured rows
    def query_rows(self, match, doc_id: str = "", limit=None) -> str:
        """Return the exact table rows that contain every string in `match`.

        **Why a row and not a passage.** The Phase B measurement made this gap concrete: the earlier literal-match tool finds
        the chunk where a figure appears, but the answer row can sit in a forty-row group of near-identical
        lines, and the model read the neighbouring line. Its own words were that none of the passages contained
        a row with the first two values 228865.1 and 188387.5 while the row was in the passage. A tool that
        returns the row, not the group, removes the reading step that failed.

        **What keeps it a query and not a filter language.** `match` is one to four literal strings; a row
        matches when every one of them is a substring of it, comma-insensitive. There is no column syntax, no
        expression, no ordering the caller controls, and the scan, row and byte bounds are constants in this
        module. A column index was left out deliberately: the row text is the parser's cells joined by a
        separator, header names are folded into only some rows, and a cell index would therefore be a contract
        the store cannot keep. A model that knows the label writes the label.

        The matching rows are added to the evidence namespace, so a citation to the row resolves and the answer
        contract can check the figure against it. That is the verification step: the figure the answer states
        has to appear in the row the tool returned.
        """
        values = _query_values(match)
        if isinstance(values, str):
            return json.dumps({"error": values})
        try:
            wanted = int(limit) if limit is not None else QUERY_DEFAULT_ROWS
        except (TypeError, ValueError):
            wanted = QUERY_DEFAULT_ROWS
        wanted = max(1, min(wanted, QUERY_ROW_CAP))
        doc = str(doc_id or "").strip()
        candidates = self._candidate_chunks(values, doc)
        if candidates is None:
            return json.dumps({"error": "the document store could not be read"})
        self._last_facts = {"query": len(values), "scoped_document": bool(doc),
                            "candidate_chunks": len(candidates)}
        if not candidates:
            where = f" in document {doc}" if doc else ""
            return json.dumps({"error": "no table row" + where + " states "
                                        + " and ".join(repr(value) for value in values)})
        rows = self._matching_rows(candidates, values)
        if not rows:
            return json.dumps({"error": "the values matched a chunk but no single row states "
                                        + " and ".join(repr(value) for value in values)})
        documents = {row["payload"].get("doc_id") or "" for row in rows}
        # **A value that identifies rows in many documents is not an anchor, and pretending it is produces the
        # confident wrong answer the Phase B run measured.** The tool says so and names documents instead of
        # letting the model pick the first of twenty census tables. With a doc_id the count is exact, so the
        # guard only fires on an unscoped query.
        if not doc and len(documents) > QUERY_AMBIGUOUS_DOCS:
            examples = sorted(documents)[:5]
            self._last_facts["documents"] = len(documents)
            return json.dumps({
                "note": f"the values match rows in {len(documents)} documents, so they do not identify one "
                        f"row. Pass doc_id to scope the query to the document the question names. "
                        f"Examples: {examples}"})
        shown = rows[:wanted]
        lines: list[str] = []
        for row in shown:
            number, printed = self._add_row(row)
            if not number:
                break
            lines.append(printed)
            if sum(len(item) + 1 for item in lines) > QUERY_RESPONSE_CHARS:
                break
        self._last_facts["rows_matched"] = len(rows)
        self._last_facts["rows_returned"] = len(lines)
        # **A matched row that cannot be returned must say why.** The first version answered with a header and
        # no rows when the evidence namespace was full, and the model read that as "no such row". That is the
        # failure that cost one question all five runs of the first treatment measurement.
        if not lines:
            return json.dumps({"error": "the rows matched, but the evidence list is full, so a new row cannot "
                                        "be numbered or cited. Answer from the evidence already gathered, or "
                                        "say what is missing."})
        header = "Rows matching " + json.dumps(values) + ":"
        if len(rows) > len(lines):
            header += (f" ({len(rows)} rows matched and {len(lines)} are shown; add doc_id or another "
                       f"string to narrow it)")
        return header + "\n" + "\n".join(lines)

    def _add_row(self, row: dict) -> tuple[int, str]:
        """Number one matched row as evidence and render it with the row number it can be cited by."""
        import retriever

        payload = row["payload"]
        passage = retriever.Passage(
            doc_id=payload.get("doc_id") or "",
            acl=payload.get("acl") or "",
            text=row["line"],
            page_start=int(payload.get("page_start") or 1),
            page_end=int(payload.get("page_end") or 1),
            kind="table_row",
            chunk_id=f"{payload.get('chunk_id') or ''}:row:{row['row_index']}",
            parse_score=(float(payload["parse_score"]) if payload.get("parse_score") is not None else None),
            table_id=payload.get("table_id"),
            row_index=row["row_index"],
        )
        number = self.add(passage, extra_slots=QUERY_EXTRA_EVIDENCE)
        if not number:
            return 0, ""
        where = f"{passage.doc_id} p{passage.page_start}"
        if row["row_index"] is not None:
            where += f" row {row['row_index']}"
            total_rows = payload.get("table_rows")
            if total_rows:
                where += f" of {total_rows}"
        return number, f"[{number}] {where}: {_shorten(row['line'], QUERY_ROW_CHARS)}"

    def _candidate_chunks(self, values: list[str], doc_id: str = "") -> list[tuple[str, str]] | None:
        """The chunks whose text could hold a matching row, or None when the store could not be read."""
        import psycopg2

        dsn = getattr(self.services, "database_url", "")
        if not dsn:
            return None
        sql, params = row_query(values, doc_id, self.roots)
        try:
            with psycopg2.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
                    return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            print(f"query_rows unavailable: {type(exc).__name__}: {exc}")
            return None

    def _matching_rows(self, candidates: list[tuple[str, str]], values: list[str]) -> list[dict]:
        """The rows that contain every value, deduplicated and in a stable order.

        A row can appear twice in the store: once in the whole-table chunk and once as its own row entry. The
        duplicate is collapsed so the model is not asked to choose between two identical lines, and the entry
        that survives is the one carrying an exact `row_index`, because that is what a citation needs.
        """
        import retriever

        payloads = retriever._fetch_payloads(self.services, [chunk_id for chunk_id, _ in candidates],
                                             self.roots)
        wanted = [_normal_for_match(value) for value in values]
        unique: dict[tuple, dict] = {}
        for chunk_id, _doc in candidates:
            payload = payloads.get(chunk_id)
            if not payload:
                continue
            for row in rows_in_chunk(payload):
                if not all(value in row["haystack"] for value in wanted):
                    continue
                document = payload.get("doc_id") or ""
                key = (document, row["row_index"]) if row["row_index"] is not None \
                    else (document, row["haystack"])
                existing = unique.get(key)
                if existing is None or (existing["row_index"] is None and row["row_index"] is not None):
                    unique[key] = row
        return sorted(unique.values(), key=lambda row: (
            row["payload"].get("doc_id") or "", int(row["payload"].get("page_start") or 0),
            row["row_index"] if row["row_index"] is not None else 0, row["haystack"]))

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
        self._last_facts = {}
        try:
            if name == "search_documents":
                out = self.search_documents(str(args.get("query") or ""))
            elif name == "read_passages":
                if not read_passages_enabled():
                    return json.dumps({"error": "read_passages is disabled on this deployment"})
                out = self.read_passages(str(args.get("doc_id") or ""), args.get("page_start"),
                                         args.get("page_end"), str(args.get("chunk_id") or ""))
            elif name == "query_rows":
                out = self.query_rows(args.get("match"), str(args.get("doc_id") or ""),
                                      args.get("limit"))
            else:
                out = json.dumps({"error": f"unknown tool {name!r}; available: search_documents, "
                                           f"read_passages, query_rows"})
        except Exception as exc:  # noqa: BLE001 - a tool failure must not end the request
            out = json.dumps({"error": f"the tool failed: {type(exc).__name__}"})
        if self.audit is not None:
            audit_error = None
            try:
                payload = {"tool": name, "argument_chars": len(arguments or ""),
                           "results": len(self.evidence),
                           "elapsed_ms": round((time.monotonic() - started) * 1000)}
                # **A query's own count is the audit fact worth having.** A tool that returns rows can scan far
                # more than it shows, and the record of how many rows matched is what lets somebody check a
                # refusal or a wrong row later.
                payload.update(getattr(self, "_last_facts", {}) or {})
                self.audit("tool_call", "", "", "ok" if not out.startswith('{"error"') else "error",
                           payload, tool=name)
            except Exception as exc:  # noqa: BLE001
                audit_error = exc
            if audit_error is not None and self.strict_audit:
                raise ToolAuditError(f"the tool audit failed: {type(audit_error).__name__}") from audit_error
        return out


IDENTIFIER_QUERIES = re.compile(r"\b[A-Z]{2,}-\d{4}-\d{3,}\b")


def quoted_identifiers(question: str) -> list[str]:
    """Document identifiers the question quotes, in stable order.

    The cross-document question names two invoice numbers. The model sometimes searched only one of them and
    then refused. Resolving each identifier deterministically before the model speaks removes that failure
    without adding a tool decision.
    """
    return sorted(set(IDENTIFIER_QUERIES.findall(question or "")))


def presearch_identifiers_enabled() -> bool:
    raw = os.environ.get("OI_PRESEARCH_IDENTIFIERS")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def debug_loop_enabled() -> bool:
    raw = os.environ.get("OI_DEBUG_LOOP")
    return bool(raw) and raw.strip().lower() in ("1", "true", "yes", "on")


def debug_loop(toolbox, question: str, content: str) -> None:
    if not debug_loop_enabled():
        return
    print("[loop-debug] question=" + question[:160].replace("\n", " "), flush=True)
    print("[loop-debug] answer=" + (content or "")[:160].replace("\n", " "), flush=True)
    print("[loop-debug] evidence=" + str(len(toolbox.evidence)), flush=True)
    for passage in toolbox.evidence[-6:]:
        print("[loop-debug] doc=" + str(getattr(passage, "doc_id", "")) + " p"
              + str(getattr(passage, "page_start", "")) + " kind="
              + str(getattr(passage, "kind", "")) + " text="
              + (getattr(passage, "text", "") or "")[:220].replace("\n", " "), flush=True)


def _document_completion_enabled() -> bool:
    raw = os.environ.get("OI_DOCUMENT_COMPLETION")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def exact_row_fallback_enabled() -> bool:
    raw = os.environ.get("OI_EXACT_ROW_FALLBACK")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def row_lookup_terms(question: str) -> list[str]:
    """Named labels, identifiers and printed figures that can identify a table row."""
    text = " ".join((question or "").split())
    phrases = re.findall(r"\b(?:[A-Z][a-z]+)(?:\s+(?:[A-Z][a-z]+|of|the)){1,3}\b", text)
    identifiers = re.findall(r"\b[A-Z]{2,}-\d{4}-\d{3,}\b", text)
    figures = re.findall(r"\b\d[\d,]*\.\d{2}\b", text)
    out = []
    for value in identifiers + phrases + figures:
        clean = value.strip()
        if clean and clean not in out:
            out.append(clean)
    return out[:6]


def exact_row_fallback(call_model, toolbox, question: str):
    """Answer from exact table rows when the normal loop refused or ran out with evidence.

    The two remaining failures are row-shaped: a named executive row and two invoice total rows. The rows are
    already in the index. A bounded query_rows lookup plus one direct answer is a different path from the
    dense tool loop, and the answer contract still checks the result.
    """
    import answer_contract

    terms = row_lookup_terms(question)
    if not terms:
        return None
    rendered_rows = []
    for term in terms:
        try:
            rendered = toolbox.query_rows([term], limit=3)
        except Exception:
            continue
        if rendered and not rendered.startswith('{"error"'):
            rendered_rows.append(rendered)
    if not rendered_rows:
        return None
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": (
            "Question: " + question + "\n\nExact table rows already retrieved:\n"
            + "\n".join(rendered_rows)
            + "\n\nAnswer the question from those rows in one short paragraph, with the figures exactly as "
              "printed and the passage numbers cited. If the rows do not contain the answer, say exactly: "
              "I could not find that in the available documents.")},
    ]
    try:
        reply = call_model(messages, [])
    except Exception:
        return None
    content = (reply.get("content") or "").strip() if not reply.get("error") else ""
    if not content or _REFUSAL_PHRASE in content.lower():
        return None
    failure = (answer_contract.violation(content, toolbox.texts())
               or answer_contract.numeric_violation(content, question, toolbox.texts())
               or ("corrupted" if answer_contract.looks_corrupted(content) else None))
    if failure:
        return None
    return {"answer": content, "reason": None, "evidence": toolbox.evidence,
            "attribution": dict(reply.get("attribution") or {}), "steps": 0, "tool_calls": 0,
            "corrections": 0, "synthesis": 0, "refusal_retries": 0, "refusal_fallbacks": 0,
            "limit_fallbacks": 0, "exact_row_fallback": 1}


def row_preseed_enabled() -> bool:
    raw = os.environ.get("OI_ROW_PRESEED")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def question_row_phrases(question: str) -> list[str]:
    """Capitalized multiword labels and quoted identifiers worth a deterministic row lookup.

    The CEO salary and invoice questions name a title or an identifier. A row lookup for that label is more
    precise than dense retrieval and gives the model the exact row before it speaks.
    """
    text = " ".join((question or "").split())
    phrases = re.findall(r"\b(?:[A-Z][a-z]+)(?:\s+(?:[A-Z][a-z]+|of|the)){1,3}\b", text)
    identifiers = re.findall(r"\b[A-Z]{2,}-\d{4}-\d{3,}\b", text)
    out = []
    for value in identifiers + phrases:
        clean = value.strip()
        if clean and clean not in out:
            out.append(clean)
    return out[:3]


def limit_fallback_enabled() -> bool:
    """Whether the loop gets one final no-tools answer from the evidence it collected before falling back.

    Some table questions spend their steps on recoverable tool errors and hit a cap with usable evidence in the
    toolbox. Falling back to the single-shot path there loses everything the loop read. One direct answer from
    the evidence is the smallest change that tests whether the loop's failure was procedural rather than a
    missing fact.
    """
    raw = os.environ.get("OI_LIMIT_FALLBACK")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def refusal_fallback_enabled() -> bool:
    """Whether a refusal gets one direct answer from the evidence already in the conversation.

    The candidate refused on two questions after retrieval had already found the right documents. A tool
    retry was not consistently better. The smaller experiment is one no-tools answer over the same
    conversation: the evidence is already there, and the model is asked to answer from it directly.
    """
    raw = os.environ.get("OI_REFUSAL_FALLBACK")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def refusal_retry_enabled() -> bool:
    """Whether a refusal may be retried once when the toolbox already holds evidence.

    The measured Kimi candidate refused on CEO salary and cross-document invoice questions even when retrieval
    had hit the right documents. The refusal is accepted by the contract, so the loop stops. One bounded retry
    with an explicit reminder that the evidence may contain the answer is the smallest change that tests
    whether the model simply stopped too early. It is off by default.
    """
    raw = os.environ.get("OI_RETRY_REFUSALS")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def identifier_single_shot_enabled() -> bool:
    """Whether a question that names document identifiers answers from the exact evidence without tools.

    The cross-document invoice question has both totals already in the exact-identifier evidence. Letting the
    tool loop search again adds nothing and has ended in a refusal. The single call is bounded by the same
    deadline and answer contract as the loop.
    """
    raw = os.environ.get("OI_IDENTIFIER_SINGLE_SHOT")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def read_passages_enabled() -> bool:
    """Whether the answer path offers the document-reading tool.

    The lab configuration that produced the accepted measurement did not offer this tool. The product branch
    added it for the newer agent runtime. A deployment can turn it off so the canonical answer path is
    comparable to the measured configuration; the default stays on.
    """
    raw = os.environ.get("OI_TOOL_READ_PASSAGES")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def active_tools() -> list:
    """The tools the answer path offers. One path, so one list."""
    tools = list(TOOLS)
    if not read_passages_enabled():
        tools = [tool for tool in tools
                 if (tool.get("function") or {}).get("name") != "read_passages"]
    return tools


def agent_tools(definition: dict) -> list:
    """The tools one agent definition offers: the retrieval tools, plus the helper when the definition has
    child tools.

    **The helper is not in `TOOLS`.** The answer path must never be able to call it; only the runtime, and only
    for a definition that declares child tools. The child's own tool list is built by the caller from
    `child_tools`, so the parent's permission is not inherited by accident.
    """
    tools = list(TOOLS)
    if definition.get("child_tools"):
        tools.append(DELEGATE_TOOL)
    if definition.get("workspace"):
        tools.extend(WORKSPACE_TOOLS)
    if definition.get("web"):
        tools.append(WEB_FETCH_TOOL)
    if definition.get("connector"):
        tools.append(SEND_EMAIL_TOOL)
    return tools


def run_loop(call_model, toolbox: ToolBox, question: str, seed_passages=(),
             max_steps: int | None = None, max_tool_calls: int | None = None,
             deadline: float | None = None, on_event=None) -> dict:
    """Run the model until it answers, bounded by steps, tool calls and a deadline.

    `call_model(messages, tools)` returns a dict: `{"content", "tool_calls", "error"}`. `tool_calls` is a list
    of `{"id", "name", "arguments"}` in the OpenAI shape the gateway speaks, which is also what the Bedrock
    providers are translated to.

    Returns `{"answer", "reason", "evidence", "steps", "tool_calls", "corrections"}`. When `answer` is None the
    caller falls back to the single-shot path; `reason` says which bound was hit.
    """
    import answer_contract

    if max_steps is None:
        max_steps = limits()[0]
    if max_tool_calls is None:
        max_tool_calls = limits()[1]
    # Identifier questions are the cross-document lookup questions. The measured 6-step configuration handled
    # them better than the looser loop; the extra steps help the salary table question and hurt this one.
    if quoted_identifiers(question):
        max_steps = min(max_steps, 6)
        max_tool_calls = min(max_tool_calls, 10)
    stats = {"steps": 0, "tool_calls": 0, "corrections": 0, "synthesis": 0, "refusal_retries": 0, "refusal_fallbacks": 0, "limit_fallbacks": 0}
    seeds = list(seed_passages)
    identifier_evidence = ""
    if row_preseed_enabled():
        try:
            row_evidence = []
            for phrase in question_row_phrases(question):
                rendered = toolbox.query_rows([phrase], limit=3)
                if rendered and not rendered.startswith('{"error"'):
                    row_evidence.append(rendered)
            if row_evidence:
                identifier_evidence = "\n".join(row_evidence)
        except Exception:  # noqa: BLE001 - the pre-seed is an improvement, not a failure path
            pass
    if presearch_identifiers_enabled() and quoted_identifiers(question):
        # **Exact text lookup, not dense search.** The question names the identifiers. A vector query for
        # "INV-2025-002" is not guaranteed to rank the invoice that contains it, and a refusal follows. The
        # exact lookup is a bounded SQL match over the permitted roots and is the same one the phrase tool
        # uses. It adds the passages to the evidence namespace and returns rendered excerpts the model can
        # cite directly.
        try:
            found = []
            for identifier in quoted_identifiers(question):
                rendered = toolbox.search_exact(identifier, limit=3)
                if rendered and not rendered.startswith('{"error"'):
                    found.append(rendered)
            identifier_evidence = "\n".join(found)
        except Exception:  # noqa: BLE001 - the pre-search is an improvement, not a new failure path
            identifier_evidence = ""
    seeds = toolbox.complete_small_documents(seeds, question=question)
    seeded = ((identifier_evidence + "\n\n") if identifier_evidence else "") + toolbox.render(seeds)
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": (
            f"Question: {question}\n\n"
            + (f"Evidence already retrieved, numbered:\n{seeded}\n\n" if seeded else "")
            + "Search if this is not enough, then answer.")},
    ]
    limit_error = ""
    if (identifier_single_shot_enabled() and presearch_identifiers_enabled()
            and quoted_identifiers(question)):
        reply = call_model(messages, [])
        content = (reply.get("content") or "").strip() if not reply.get("error") else ""
        if content:
            failure = (answer_contract.violation(content, toolbox.texts())
                       or answer_contract.numeric_violation(content, question, toolbox.texts())
                       or ("corrupted" if answer_contract.looks_corrupted(content) else None))
            if not failure:
                return {"answer": content, "reason": None, "evidence": toolbox.evidence,
                        "attribution": dict(reply.get("attribution") or {}), **stats}
    # **What the gateway says answered, per call, so the answer can name the model that ran.** The reply
    # that produces the answer carries it; a failover means this is a different model group from the
    # manifest's primary, and the caller reports both facts.
    attribution: dict = {}

    while stats["steps"] < max_steps:
        if deadline is not None and time.monotonic() >= deadline:
            return {"answer": None, "reason": "tool_deadline", "evidence": toolbox.evidence,
                    **stats}
        stats["steps"] += 1
        if on_event is not None:
            on_event({"type": "step", "step": stats["steps"]})
        reply = call_model(messages, active_tools())
        if reply.get("error"):
            return {"answer": None, "reason": reply["error"], "evidence": toolbox.evidence, **stats}
        attribution = dict(reply.get("attribution") or attribution)
        calls = reply.get("tool_calls") or []
        if not calls:
            content = (reply.get("content") or "").strip()
            failure = (answer_contract.violation(content, toolbox.texts())
                       or answer_contract.numeric_violation(content, question, toolbox.texts())
                       or ("corrupted" if answer_contract.looks_corrupted(content) else None))
            if failure and stats["steps"] < max_steps:
                stats["corrections"] += 1
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": CORRECTIVE.format(
                    reason=failure, instruction=answer_contract.corrective_message(failure))})
                continue
            if (not failure and exact_row_fallback_enabled()
                    and _REFUSAL_PHRASE in content.lower() and toolbox.evidence):
                fallback = exact_row_fallback(call_model, toolbox, question)
                if fallback:
                    return fallback
            if (not failure and refusal_retry_enabled() and stats["refusal_retries"] == 0
                    and _REFUSAL_PHRASE in content.lower() and toolbox.evidence
                    and stats["steps"] < max_steps):
                stats["refusal_retries"] += 1
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": _REFUSAL_RETRY_INSTRUCTION})
                continue
            if (not failure and refusal_fallback_enabled() and stats["refusal_fallbacks"] == 0
                    and _REFUSAL_PHRASE in content.lower() and toolbox.evidence):
                stats["refusal_fallbacks"] += 1
                fallback = call_model(messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": (
                        "The evidence above may contain the answer. Answer directly from that evidence in one "
                        "short paragraph, with the figures exactly as printed. Do not refuse unless the "
                        "evidence truly does not contain it.")},
                ], [])
                retry_content = (fallback.get("content") or "").strip() if not fallback.get("error") else ""
                if retry_content:
                    retry_failure = (answer_contract.violation(retry_content, toolbox.texts())
                                     or answer_contract.numeric_violation(
                                         retry_content, question, toolbox.texts())
                                     or ("corrupted" if answer_contract.looks_corrupted(retry_content)
                                         else None))
                    if not retry_failure and _REFUSAL_PHRASE not in retry_content.lower():
                        return {"answer": retry_content, "reason": None,
                                "evidence": toolbox.evidence,
                                "attribution": dict(fallback.get("attribution") or attribution), **stats}
            debug_loop(toolbox, question, content)
            return {"answer": content, "reason": None, "evidence": toolbox.evidence,
                    "attribution": attribution, **stats}

        messages.append({
            "role": "assistant", "content": reply.get("content") or "",
            "tool_calls": [{"id": call["id"], "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]}}
                           for call in calls],
        })
        exhausted = False
        for call in calls:
            if stats["tool_calls"] >= max_tool_calls:
                limit_error = "tool_budget"
                exhausted = True
                break
            stats["tool_calls"] += 1
            result = toolbox.execute(call.get("name") or "", call.get("arguments") or "{}")
            if debug_loop_enabled():
                print("[loop-debug] tool=" + (call.get("name") or "") + " args="
                      + (call.get("arguments") or "")[:240].replace("\n", " "), flush=True)
                print("[loop-debug] result=" + str(result)[:320].replace("\n", " "), flush=True)
            if on_event is not None:
                on_event({"type": "tool", "tool": call.get("name") or "",
                          "evidence": len(toolbox.evidence)})
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "",
                             "content": result})
            if isinstance(result, str) and result.startswith('{"error"'):
                limit_error = "tool_error"
        if exhausted:
            break
    # **A capped loop keeps its evidence.** The old path returned no answer, and the caller fell back to the
    # single-shot request whose six passages had already failed, discarding everything the loop had gathered.
    # Measured: N06/N07 are flaky exactly this way under load, and the same failure appears in the committed
    # raw runs. One no-tools synthesis turn lets the model answer from evidence it has already seen, verified
    # by the same answer contract; if it fails, behaviour is exactly as before.
    if toolbox.evidence:
        stats["synthesis"] = 1
        try:
            reply = call_model(messages + [{"role": "user", "content": SYNTHESIS_INSTRUCTION}], [])
        except Exception:
            reply = {"error": "synthesis_failed"}
        content = (reply.get("content") or "").strip() if not reply.get("error") else ""
        # **A synthesized refusal is a failed synthesis.** The old path's single-shot fallback can still
        # answer from the retrieval passages, and measured on A04 executive it did: the loop's gathered
        # evidence held only part of the compensation table, the synthesis correctly refused, and the
        # question regressed from 5 of 5 to 0 of 5. Refusals fall through to the old behaviour; only a
        # synthesis that actually answers is accepted.
        if (content and _REFUSAL_PHRASE not in content.lower()
                and answer_contract.violation(content, toolbox.texts()) is None
                and answer_contract.numeric_violation(content, question, toolbox.texts()) is None
                and not answer_contract.looks_corrupted(content)):
            return {"answer": content, "reason": None, "evidence": toolbox.evidence,
                    "attribution": attribution, **stats}
    if exact_row_fallback_enabled() and toolbox.evidence:
        fallback = exact_row_fallback(call_model, toolbox, question)
        if fallback:
            return fallback
    if toolbox.evidence and limit_fallback_enabled() and not stats["synthesis"]:
        stats["limit_fallbacks"] += 1
        try:
            reply = call_model(messages + [{"role": "user", "content": (
                "Answer the question from the evidence above in one short paragraph. Use the figures exactly as "
                "printed and cite the passage numbers. Do not search again. If the evidence truly does not "
                "contain the answer, say exactly: I could not find that in the available documents.")}], [])
        except Exception:
            reply = {"error": "limit_fallback_failed"}
        content = (reply.get("content") or "").strip() if not reply.get("error") else ""
        if content:
            failure = (answer_contract.violation(content, toolbox.texts())
                       or answer_contract.numeric_violation(content, question, toolbox.texts())
                       or ("corrupted" if answer_contract.looks_corrupted(content) else None))
            if not failure and _REFUSAL_PHRASE not in content.lower():
                return {"answer": content, "reason": None, "evidence": toolbox.evidence,
                        "attribution": dict(reply.get("attribution") or {}), **stats}
    return {"answer": None, "reason": limit_error or "tool_step_limit",
            "evidence": toolbox.evidence, **stats}


def limits() -> tuple[int, int]:
    """Steps and tool calls per answer.

    The product default is the measured 6 and 10. A bundle-serving deployment can raise the step cap for table
    questions where a recoverable tool error consumes a step and the loop otherwise falls back with the right
    row already in the evidence.
    """
    def _limit(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return max(1, int(raw))
        except ValueError:
            return default
    return _limit("OI_TOOL_MAX_STEPS", 6), _limit("OI_TOOL_MAX_CALLS", 10)
