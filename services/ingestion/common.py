"""Shared pieces for the document pipeline: configuration, parsing, chunking, and clients.

Two deliberate choices live here.

**Chunks never cross a page boundary.** A citation that says "page 4" has to be true, so chunks are
cut within a page rather than across pages. Slightly smaller chunks near a page break are a fair
price for citations a human can check.

**Every chunk carries its access label.** Access is decided at retrieval time from the label, not at
display time. A document the user may not read is never retrieved, so it is never in the prompt and
can never be summarised by accident.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

DOCS_DIR = Path(os.environ.get("DOCS_DIR", "/app/documents"))
COLLECTION = os.environ.get("COLLECTION", "company_docs")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
EMBEDDINGS_URL = os.environ.get("EMBEDDINGS_URL", "http://embeddings:80")
RERANKER_URL = os.environ.get("RERANKER_URL", "http://reranker:80")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://litellm:4000")
GATEWAY_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL")
DEPLOYMENT_ID = os.environ.get("DEPLOYMENT_ID", "unknown")
VECTOR_SIZE = int(os.environ.get("VECTOR_SIZE", "1024"))       # bge-m3
CHUNK_CHARS = int(os.environ.get("CHUNK_CHARS", "900"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "150"))
PAGE_CHARS = int(os.environ.get("PAGE_CHARS", "3000"))         # for formats with no pages

# Which document folders each role may read. The top-level folder under documents/ becomes the ACL,
# so adding a restricted class of documents is a matter of creating a folder.
ROLE_ACLS: dict[str, set[str]] = {
    "admin": {"company", "executive"},
    "staff": {"company"},
}


def qdrant() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, timeout=30)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def doc_id_for(path: Path) -> str:
    """Relative path, so the folder name carries the ACL."""
    return str(path.relative_to(DOCS_DIR)).replace(os.sep, "/")


def acl_for(doc_id: str) -> str:
    return doc_id.split("/", 1)[0] if "/" in doc_id else "company"


# --------------------------------------------------------------------------- parsing

def _pages_pdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [(page.extract_text() or "") for page in reader.pages]


def _pages_docx(path: Path) -> list[str]:
    import docx

    document = docx.Document(str(path))
    text = "\n".join(p.text for p in document.paragraphs)
    return [text] if text.strip() else []


def _pages_html(path: Path) -> list[str]:
    from html.parser import HTMLParser

    class Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            if data.strip():
                self.parts.append(data.strip())

    parser = Text()
    parser.feed(path.read_text(errors="replace"))
    text = "\n".join(parser.parts)
    return [text] if text.strip() else []


def _pages_text(path: Path) -> list[str]:
    text = path.read_text(errors="replace")
    # No real pages, so synthesise citable ones. A citation to "page 1 of 3" is still useful.
    if len(text) <= PAGE_CHARS:
        return [text]
    return [text[i:i + PAGE_CHARS] for i in range(0, len(text), PAGE_CHARS)]


PARSERS = {".pdf": _pages_pdf, ".docx": _pages_docx, ".html": _pages_html, ".htm": _pages_html,
           ".md": _pages_text, ".markdown": _pages_text, ".txt": _pages_text, ".csv": _pages_text}


def parse(path: Path) -> list[str]:
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"no parser for {path.suffix}")
    return [page for page in parser(path)]


# --------------------------------------------------------------------------- chunking

@dataclass
class Chunk:
    page: int
    index: int
    text: str


def chunk_pages(pages: list[str], size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    chunks: list[Chunk] = []
    index = 0
    for page_number, page in enumerate(pages, start=1):
        text = re.sub(r"[ \t]+", " ", page).strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            piece = text[start:start + size].strip()
            if len(piece) > 40:                      # ignore slivers from page ends
                chunks.append(Chunk(page=page_number, index=index, text=piece))
                index += 1
            if start + size >= len(text):
                break
            start += size - overlap
    return chunks


# --------------------------------------------------------------------------- services

def embed(texts: list[str]) -> list[list[float]]:
    with httpx.Client(timeout=120) as client:
        response = client.post(f"{EMBEDDINGS_URL}/embed", json={"inputs": texts})
        response.raise_for_status()
        return response.json()


def rerank(query: str, texts: list[str]) -> list[tuple[int, float]]:
    with httpx.Client(timeout=120) as client:
        response = client.post(f"{RERANKER_URL}/rerank", json={"query": query, "texts": texts})
        response.raise_for_status()
        return [(item["index"], item["score"]) for item in response.json()]


def ensure_collection(client: QdrantClient, recreate: bool = False) -> None:
    exists = client.collection_exists(COLLECTION)
    if exists and recreate:
        client.delete_collection(COLLECTION)
        exists = False
    if not exists:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=qm.VectorParams(size=VECTOR_SIZE, distance=qm.Distance.COSINE),
        )
        for field in ("doc_id", "acl"):
            client.create_payload_index(COLLECTION, field_name=field, field_schema=qm.PayloadSchemaType.KEYWORD)


def delete_document(client: QdrantClient, doc_id: str) -> None:
    client.delete(
        collection_name=COLLECTION,
        points_selector=qm.FilterSelector(filter=qm.Filter(must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])),
    )


def upsert_chunks(client: QdrantClient, doc_id: str, acl: str, source: str,
                  chunks: list[Chunk], vectors: list[list[float]], digest: str) -> None:
    points = [
        qm.PointStruct(
            id=hashlib.md5(f"{doc_id}:{chunk.index}".encode()).hexdigest(),   # stable id per chunk
            vector=vector,
            payload={"doc_id": doc_id, "source": source, "page": chunk.page, "chunk": chunk.index,
                     "acl": acl, "sha256": digest, "text": chunk.text},
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    client.upsert(collection_name=COLLECTION, points=points)


# --------------------------------------------------------------------------- audit

def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def record_event(event_type: str, actor: str, *, actor_role: str | None = None,
                 data_class: str = "internal", outcome: str = "ok", tool: str | None = None,
                 model: str | None = None, request_id: str | None = None,
                 payload: dict | None = None) -> int | None:
    """Append one event to the audit sink. The trigger builds the hash chain."""
    if not DATABASE_URL:
        return None
    body = payload or {}
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """insert into audit_events
                 (event_type, actor, actor_role, deployment_id, data_class, outcome, tool, model,
                  request_id, payload_sha256, payload)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (event_type, actor, actor_role, DEPLOYMENT_ID, data_class, outcome, tool, model,
             request_id, payload_hash(body), json.dumps(body)),
        )
        return cur.fetchone()[0]
