"""Reading a document the answer cited, so a person can check the claim rather than trust it.

**Why this exists.** The Ask screen shows a chip per source and the sentence that says an answer came from
four of six documents. Until now the chips were spans: a person could see that an answer rested on the travel
policy and could not read the travel policy. A citation nobody can open is a citation that asks to be believed,
and this product's whole claim is that it does not ask.

**Where the text comes from, and why not the file on disk.** The ingestion worker writes every chunk to
`chunk_index` as it writes it to the vector store, so this reads the text the system actually indexed and the
model actually answered from, with the page it came from. Serving the file from the documents root instead
would show a person something subtly different from what the answer used: a PDF's layout, a spreadsheet's
rendering, an OCR'd page as an image. **The honest thing to show is what was read.**

**The access label is filtered in the query**, the same rule the retrieval arms follow and for the same reason:
a second path to content is where a missing filter hides. `chunk_index.acl` is in the where clause, not applied
after it.

**A document outside the caller's roots is not found rather than forbidden.** The wording matters: answering
"forbidden" for a document that exists tells a caller it exists, and the API already answers 404 for a job
belonging to somebody else. One answer for both cases is the same argument the session refusal makes.
"""
from __future__ import annotations

import json
import os

# How much of a document leaves in one response. A 60-page scanned PDF is megabytes of text and a browser
# wants the part it is being asked to check. The cap is reported rather than silent, because a document view
# that quietly showed the first sixty chunks would make a person think they had read the whole thing.
MAX_CHUNKS = int(os.environ.get("OI_MAX_DOCUMENT_CHUNKS", "200"))

# `acl` rides along so the caller can say which permission the document came under, the way the citation
# chip does, without a second query.
READ_COLUMNS = "chunk_id, text, page_start, page_end, kind, acl"

# **The label filter is inside the query, and that is the whole security property of this module.** The first
# two arms of retrieval each apply it; this is the third path to the same content and the place a missing
# filter would go unnoticed, because every test written against a permitted document passes either way.
READ_SQL = """
    select {columns}
      from chunk_index
     where doc_id = %s and acl = any(%s)
     order by page_start, page_end, chunk_id
     limit %s
""".format(columns=READ_COLUMNS)

# The extent of a document, so a caller can say "pages 2 to 3 of a 6 page document" without holding the text.
EXTENT_SQL = """
    select min(page_start), max(page_end), count(*)
      from chunk_index
     where doc_id = %s and acl = any(%s)
"""


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def read(session, *, database_url: str | None = None, doc_id: str, max_chunks: int = MAX_CHUNKS):
    """The indexed text of one document, or None when this session may not read it.

    Returns a plain dict so the caller can put it straight into a response model, and so an acceptance probe
    can assert on it without importing the API's pydantic classes.
    """
    import psycopg

    url = database_url or globals()["database_url"]()
    if not url:
        raise RuntimeError("no DATABASE_URL, so the document index cannot be read")

    roots = sorted(session.permit().roots)
    if not roots:
        # A session with no roots reaches nothing. This is not an error: it is the answer.
        return None

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(EXTENT_SQL, (doc_id, roots))
            extent = cur.fetchone()
            if not extent or not extent[2]:
                return None
            cur.execute(READ_SQL, (doc_id, roots, max_chunks))
            rows = cur.fetchall()

    import redaction

    chunks = []
    counts: dict[str, int] = {}
    unverified = 0
    acl = rows[0][5] if rows else ""
    for chunk_id, text, page_start, page_end, kind, acl in rows:
        # **Every chunk is redacted on the way out**, by the same function that redacts an answer. A document
        # is the client's own material and a reserved identifier in it is still a reserved identifier: the
        # control is that it does not reach a browser, and "they are allowed to read the file" is not an
        # argument for putting an SSN on a screen somebody may be sharing.
        safe, report = redaction.redact(text)
        for kind_name, count in report.counts.items():
            counts[kind_name] = counts.get(kind_name, 0) + count
        unverified += report.unverified_cards
        chunks.append({
            "chunk_id": chunk_id,
            "page_start": page_start,
            "page_end": page_end,
            "kind": kind,
            "text": safe,
        })

    total = extent[2]
    return {
        "doc_id": doc_id,
        "acl": acl,
        "page_start": extent[0],
        "page_end": extent[1],
        "chunk_count": total,
        "chunks": chunks,
        "truncated": total > len(chunks),
        "redactions": dict(sorted(counts.items())),
        "unverified_card_shapes": unverified,
    }


def as_json(document: dict) -> str:
    """For the probe and for an operator reading a document without a browser."""
    return json.dumps(document, indent=2, sort_keys=True)
