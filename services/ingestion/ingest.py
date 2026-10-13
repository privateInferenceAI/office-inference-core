"""The ingestion worker: a document on disk becomes chunks in the vector store, once.

What it owns, and what it deliberately does not. It owns the decisions that make a re-run safe: hashing
the file before parsing so an unchanged one costs nothing, writing chunk ids that are stable across runs,
and removing the chunks of a document that changed or left. It does not own parsing or chunking, which are
the parser service and `chunker.py`, and it does not own the record, which the audit sink does.

The order of operations is the design. Hash first, because the hash is what makes the skip cheap and a
changed file detectable. Parse second. Chunk third. Embed fourth, in batches, because the embedding
service is the slowest step and one request per chunk would be wasteful. Write last, and only after
everything succeeded, because a partial write is worse than no write: chunks for half a document are
retrievable, and the citation from them looks exactly as confident as a correct one.

Deletions are reconciled from what is present. A document in the manifest whose file has gone has its
chunks removed. That direction matters: reconciling from the manifest rather than from the filesystem
means a file that was never ingested cannot cause a removal, and a transient mount failure cannot delete
a client's index.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chunker  # noqa: E402

# How many chunks to send to the embedding service in one request. The service advertises a client batch
# limit of 32 texts, and this stays under it. Read from the environment because a service declaration that
# promises a knob the code does not read is a lie told in documentation, and this project has already paid
# for one of those on the parser options.
EMBED_BATCH = int(os.environ.get("INGEST_EMBED_BATCH", "16"))

# A file that fails this many times is dead-lettered rather than retried every cycle. Two, because one
# failure is usually transient and an unreadable file stays unreadable.
MAX_FAILURES = int(os.environ.get("INGEST_MAX_FAILURES", "2"))


def is_transient(exc: BaseException) -> bool:
    """Whether a failure says something about the moment rather than about the document.

    The distinction matters because dead-lettering is for documents, not for outages. The first real corpus
    run dead-lettered a 60-page PDF after two 504s from the parser, and the document was fine: the stage
    behind it is slow and something in front of it gave up. Counting that against the file means a
    permanently unreadable corpus that no retry will fix and no log explains, because the entry says the
    document failed rather than that the service did.

    Transient means a gateway error, a timeout, a refused or dropped connection. Everything else is treated
    as a property of the document, because guessing the other way would retry a corrupt file forever.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRY_STATUSES
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))

# Where the last cycle's result is written, for the healthcheck to read. A worker with no listening socket
# cannot be asked whether it is well, so it records the answer instead.
STATE_PATH = Path(os.environ.get("INGEST_STATE_PATH", "/state/last-cycle.json"))

# File types the pipeline accepts. Anything else is ignored rather than failed, because a client's
# document folder will contain a .DS_Store and a shortcut sooner or later, and neither is an error.
ACCEPTED_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown",
                     ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp"}


def sha256_of(path: Path) -> tuple[str, int]:
    """The file's bytes, hashed, and its size. Read once, streamed, because a client's PDF can be large."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


# --------------------------------------------------------------------------- the two services

@dataclass
class Services:
    """The three things this worker talks to, so a test can point them anywhere."""

    parser_url: str = field(default_factory=lambda: os.environ.get("PARSER_URL", "http://parser:5001"))
    embed_url: str = field(default_factory=lambda: os.environ.get("EMBED_URL", "http://embeddings:80"))
    vector_url: str = field(default_factory=lambda: os.environ.get("VECTOR_URL", "http://vector:6333"))
    collection: str = field(default_factory=lambda: os.environ.get("COLLECTION", "documents"))
    timeout: int = 900
    # Discovered once per run, because the embedding service has had more than one batch endpoint and
    # asking which one answers is cheaper than being wrong about it on every chunk.
    embed_path: str | None = None


# Statuses worth trying again. A 5xx from a gateway in front of a slow stage is the transient case that
# matters here: parsing a 60-page PDF on CPU takes minutes, and a proxy in front of the parser gave up
# with a 504 on the first real corpus run. A 4xx is not retried, because the same request will fail the
# same way and retrying it turns a clear error into a slow one.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 5


def _with_retries(describe: str, call) -> dict:
    """Run one request, retrying the failures that are worth retrying.

    Said out loud when it retries, because a run that silently takes three times as long is harder to
    diagnose than one that says why it is waiting.
    """
    last: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return call()
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUSES or attempt == RETRY_ATTEMPTS:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == RETRY_ATTEMPTS:
                raise
            last = exc
        wait = RETRY_BACKOFF_SECONDS * attempt
        print(f"  {describe} failed with {last}; retrying in {wait}s "
              f"(attempt {attempt} of {RETRY_ATTEMPTS})")
        time.sleep(wait)
    raise RuntimeError(f"{describe} failed after {RETRY_ATTEMPTS} attempts: {last}")


def _post(url: str, body: dict, timeout: int, method: str = "POST") -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def _get(url: str, timeout: int) -> dict | None:
    """A GET, where 404 is an answer rather than an error. Returns None when the thing is not there."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw).get("result", {}) if raw else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _multipart(url: str, filename: str, payload: bytes, options: dict, timeout: int) -> dict:
    """A multipart POST with each option as its own form field.

    Learned the hard way in step 3.4: the parser ignores an options object nested under a single field,
    answers `status: success`, and returns a document that does not have what was asked for. Each option
    goes in separately, and the caller checks for the content rather than for the status.
    """
    boundary = "----oicoreingest"
    body = bytearray()

    def part(headers: list[str], content: bytes) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        for header in headers:
            body.extend(f"{header}\r\n".encode())
        body.extend(b"\r\n")
        body.extend(content)
        body.extend(b"\r\n")

    part([f'Content-Disposition: form-data; name="files"; filename="{filename}"',
          "Content-Type: application/octet-stream"], payload)
    for key, value in options.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, bool):
                item = str(item).lower()
            part([f'Content-Disposition: form-data; name="{key}"'], str(item).encode())
    body.extend(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url, data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def parse_document(services: Services, path: Path, relpath: str) -> dict:
    """The parser's structured output for one file.

    Retried, because this is the slow stage and the first real corpus run lost a 60-page PDF to a 504 from
    whatever sits in front of the parser. The parse itself is idempotent, so trying again costs time and
    nothing else.
    """
    payload = _with_retries(
        f"parsing {relpath}",
        lambda: _multipart(
            f"{services.parser_url}/v1/convert/file", path.name, path.read_bytes(),
            {"to_formats": "json", "do_ocr": True, "do_table_structure": True,
             "table_mode": "accurate", "image_export_mode": "placeholder"},
            services.timeout))
    document = (payload.get("document") or {}).get("json_content") or {}
    if not document:
        raise RuntimeError(
            f"the parser returned no structured output for {relpath}. Its status was "
            f"{payload.get('status')!r}; an empty json_content usually means the options were not "
            f"accepted, which is what step 3.4 spent a round trip on.")
    return document


def _ocr_ran(parsed: dict) -> bool:
    """Whether the parser read this document with OCR rather than from a text layer.

    Decided from whether any text item carries OCR provenance. Docling marks the origin of each item, so a
    document with a text layer reports one origin and a scan reports another. Where the report is absent
    the answer is True, because assuming OCR is the direction that keeps a machine-read figure labelled as
    machine-read rather than presenting it as digital text.
    """
    for item in parsed.get("texts") or []:
        for prov in item.get("prov") or []:
            origin = prov.get("origin")
            if isinstance(origin, str):
                if "ocr" in origin.lower():
                    return True
                if "native" in origin.lower() or "digital" in origin.lower():
                    return False
    return True


def embed_texts(services: Services, texts: list[str]) -> list[list[float]]:
    """Vectors for a batch of chunks, from whichever embedding endpoint answers.

    The path is discovered once and remembered. `/embed` and `/embeddings` have both served this purpose
    across versions, and `tests/fixtures/api_contract_probe.py` is what tells us which one this
    deployment runs rather than this code guessing.
    """
    candidates = [services.embed_path] if services.embed_path else ["/embed", "/embeddings",
                                                                    "/v1/embeddings"]
    last_error: Exception | None = None
    for path in candidates:
        if not path:
            continue
        try:
            if path == "/v1/embeddings":
                payload = _post(f"{services.embed_url}{path}",
                                {"input": texts, "model": "bge-m3"}, services.timeout)
                vectors = [item["embedding"] for item in payload.get("data", [])]
            else:
                payload = _post(f"{services.embed_url}{path}", {"inputs": texts}, services.timeout)
                vectors = payload if isinstance(payload, list) else payload.get("data", [])
            if vectors and isinstance(vectors[0], list):
                services.embed_path = path
                return vectors
        except urllib.error.HTTPError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"no embedding endpoint answered. Last error: {last_error}")


def _get_vector_size(services: Services) -> int:
    """How many dimensions the embedding service actually produces.

    Measured rather than declared, because the number that matters is the model's, and a constant in this
    file is a second place for it to be wrong. The probe confirmed 1024 for bge-m3 on this deployment.
    """
    for path in ("/embed", "/v1/embeddings"):
        try:
            if path == "/v1/embeddings":
                payload = _post(f"{services.embed_url}{path}",
                                {"input": ["dimension probe"], "model": "bge-m3"}, services.timeout)
                vector = (payload.get("data") or [{}])[0].get("embedding")
            else:
                payload = _post(f"{services.embed_url}{path}", {"inputs": "dimension probe"},
                                services.timeout)
                if isinstance(payload, dict):
                    vector = ((payload.get("data") or [{}])[0] or {}).get("embedding")
                elif isinstance(payload, list) and payload and isinstance(payload[0], list):
                    vector = payload[0]
                elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
                    vector = payload[0].get("embedding")
                else:
                    vector = None
            if vector:
                return len(vector)
        except Exception:  # noqa: BLE001 - try the next shape, and report if none work
            continue
    raise RuntimeError(
        "could not measure the embedding dimension: no endpoint returned a vector. Without it a "
        "collection cannot be created correctly, and creating one at a guessed size is worse than "
        "failing, because every write would be rejected later with a message about vector size rather "
        "than about this.")


def ensure_collection(services: Services) -> int:
    """Make sure the collection exists with the embedding model's dimension, and say what was found.

    Why the worker does this rather than a manual step. The collection is a dependency of every write,
    and the first run of this worker against a fresh vector store failed every document with a 404 while
    its container reported healthy. A manual step that lives in a runbook is one the next person does not
    know about, and the failure it leaves behind is a per-document error that reads like a parsing
    problem.

    It refuses to reconcile a mismatch. A collection built at a different size than the model produces
    means either the model changed or the collection did, and both are decisions rather than accidents:
    silently recreating it would delete a client's index, and silently using it would reject every write.
    The error says which.
    """
    existing = _get(f"{services.vector_url}/collections/{services.collection}",
                    services.timeout)
    measured = _get_vector_size(services)

    if existing is None:
        _post(f"{services.vector_url}/collections/{services.collection}",
              {"vectors": {"size": measured, "distance": "Cosine"}}, services.timeout,
              method="PUT")
        print(f"created collection {services.collection} at {measured} dimensions, cosine distance")
        return measured

    params = ((existing.get("config") or {}).get("params") or {}).get("vectors") or {}
    size = params.get("size")
    distance = params.get("distance")
    if size != measured:
        raise RuntimeError(
            f"collection {services.collection} holds {size}-dimension vectors and the embedding model "
            f"produces {measured}. This is a decision, not a repair: recreating the collection deletes "
            f"every chunk in it, and using it as it is rejects every write. Decide which side is wrong, "
            f"then either re-embed the corpus or restore the model that matched.")
    if distance and distance != "Cosine":
        raise RuntimeError(
            f"collection {services.collection} uses {distance} distance and this pipeline expects Cosine. "
            f"Search results would be ordered differently from every measurement taken so far.")
    print(f"collection {services.collection}: {size} dimensions, {distance}, "
          f"{existing.get('points_count')} points")
    return measured


def upsert_points(services: Services, points: list[dict]) -> None:
    if not points:
        return
    _post(f"{services.vector_url}/collections/{services.collection}/points?wait=true",
          {"points": points}, services.timeout, method="PUT")


def delete_document(services: Services, doc_id: str) -> None:
    """Remove every chunk of a document, by filter on its payload.

    By filter rather than by id list, because the caller does not always know the ids: a document whose
    bytes changed has new ids, and the old ones are found by which document they belong to.
    """
    _post(f"{services.vector_url}/collections/{services.collection}/points/delete?wait=true",
          {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}},
          services.timeout)


# --------------------------------------------------------------------------- the cycle

@dataclass
class Document:
    relpath: str
    root: str
    path: Path


def discover(documents_root: Path) -> list[Document]:
    """Every document under a root, recursively, with its access root as the top-level folder.

    Recursive because a client's policy folder has subfolders, and the root is the FIRST component of the
    relative path, which is what the access label is derived from. A file directly in the documents root
    gets the root's own name, so nothing is unlabelled.
    """
    found: list[Document] = []
    if not documents_root.exists():
        return found
    for path in sorted(documents_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ACCEPTED_SUFFIXES:
            continue
        relpath = str(path.relative_to(documents_root))
        root = relpath.split(os.sep, 1)[0] if os.sep in relpath else documents_root.name
        found.append(Document(relpath=relpath, root=root, path=path))
    return found


def ingest_document(services: Services, document: Document, content_sha256: str,
                    page_count: int | None = None) -> tuple[list[dict], int]:
    """One document into points, and the number of pages the parser saw.

    The chunk ids come from `chunker`, which derives them from the document id, the content hash, the
    chunker version and the chunk's own hash. So the same bytes produce the same ids, a changed file
    produces new ones, and a changed chunker produces new ones again. That is what makes a re-run an
    update rather than a duplication.
    """
    parsed = parse_document(services, document.path, document.relpath)
    chunks = chunker.chunk_document(parsed, doc_id=document.relpath, source_sha256=content_sha256)
    pages = len(parsed.get("pages") or {}) or None

    # Refuse to record a document that produced nothing. The first real corpus run wrote two contract
    # exhibits as zero chunks and reported success, which put two documents in the index that look
    # retrievable and contain no text. A failure would have been visible; this was not. The check is here
    # rather than in the chunker because the parser's output is what is being judged, and the same empty
    # result can come from a document that genuinely has no text.
    if not chunks:
        raise RuntimeError(
            f"the parser returned a document with no retrievable text for {document.relpath}: "
            f"{len(parsed.get('texts') or [])} text items, {len(parsed.get('tables') or [])} tables, "
            f"and the chunker produced no chunks. An empty document is worse than a failed one, because "
            f"it looks retrievable.")

    # Whether OCR actually produced this text is a fact this worker knows and the chunker cannot. The
    # chunker defaults the flag to True, which is the safe direction for a scanned-document pipeline and
    # the wrong answer for a digital one, so it is corrected here from the parser's own report. A figure
    # read by OCR and a figure read from a text layer carry different risk, and the payload is where that
    # difference has to survive.
    ocr_ran = _ocr_ran(parsed)

    points: list[dict] = []
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start:start + EMBED_BATCH]
        vectors = embed_texts(services, [chunk.text for chunk in batch])
        if len(vectors) != len(batch):
            raise RuntimeError(
                f"asked the embedding service for {len(batch)} vectors and got {len(vectors)}")
        for chunk, vector in zip(batch, vectors):
            payload = chunk.as_payload()
            # The access label is attached here rather than derived at query time, because the search
            # filters on it and a filter that reads the filesystem is not a filter.
            payload["acl"] = document.root
            payload["is_ocr"] = ocr_ran
            points.append({"id": chunk.chunk_id, "vector": vector, "payload": payload})
    return points, pages


# --------------------------------------------------------------------------- the manifest

class Manifest:
    """What has been ingested, behind a small interface so the policy is testable without Postgres.

    A dictionary-backed implementation is what the tests use, and it is also what runs when there is no
    database configured. That fallback is deliberate: refusing to ingest because a bookkeeping table is
    unreachable would turn a records problem into an outage, and the cost of continuing is a re-parse
    next cycle rather than a wrong answer.
    """

    def get(self, relpath: str) -> dict | None:
        raise NotImplementedError

    def all(self) -> dict[str, dict]:
        raise NotImplementedError

    def put(self, relpath: str, record: dict) -> None:
        raise NotImplementedError

    def remove(self, relpath: str) -> None:
        raise NotImplementedError

    def clear_failures(self, relpath: str | None = None) -> int:
        """Forget recorded failures, for one document or for all of them.

        Needed because a rule can change and the rows it wrote cannot know that. The first real corpus run
        dead-lettered a 60-page PDF over two gateway timeouts, and when failures learned to distinguish an
        outage from a corrupt file, that row still said "two failures" and the document stayed dead. A
        repair path is the honest answer: the alternative is telling an operator to delete rows by hand
        from a table they have no reason to know about.
        """
        raise NotImplementedError

    def start_run(self) -> int | None:
        return None

    def finish_run(self, run_id: int | None, counts: dict, note: str = "") -> None:
        return None


class MemoryManifest(Manifest):
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def get(self, relpath: str) -> dict | None:
        return self.records.get(relpath)

    def all(self) -> dict[str, dict]:
        return dict(self.records)

    def put(self, relpath: str, record: dict) -> None:
        self.records[relpath] = record

    def remove(self, relpath: str) -> None:
        self.records.pop(relpath, None)

    def clear_failures(self, relpath: str | None = None) -> int:
        cleared = 0
        for key, record in self.records.items():
            if relpath is not None and key != relpath:
                continue
            if (record.get("failure_count") or 0) or record.get("failure_kind"):
                record["failure_count"] = 0
                record["failure_kind"] = None
                record["last_error"] = None
                cleared += 1
        return cleared


class PostgresManifest(Manifest):
    """The real one. Imported lazily so the module loads without a database driver present."""

    def __init__(self, dsn: str) -> None:
        import psycopg2  # noqa: PLC0415 - deliberately lazy, see the docstring
        self.connection = psycopg2.connect(dsn)
        self.connection.autocommit = True

    def get(self, relpath: str) -> dict | None:
        with self.connection.cursor() as cur:
            cur.execute(
                "select relpath, root, content_sha256, bytes, page_count, chunk_count, "
                "failure_count, failure_kind, last_error, ingested_at "
                "from ingest_manifest where relpath = %s",
                (relpath,))
            row = cur.fetchone()
        if not row:
            return None
        keys = ("relpath", "root", "content_sha256", "bytes", "page_count", "chunk_count",
                "failure_count", "failure_kind", "last_error", "ingested_at")
        return dict(zip(keys, row))

    def all(self) -> dict[str, dict]:
        with self.connection.cursor() as cur:
            cur.execute("select relpath, root, content_sha256, chunk_count, failure_count, "
                        "failure_kind, last_error from ingest_manifest")
            rows = cur.fetchall()
        keys = ("relpath", "root", "content_sha256", "chunk_count", "failure_count", "failure_kind",
                "last_error")
        return {row[0]: dict(zip(keys, row)) for row in rows}

    def put(self, relpath: str, record: dict) -> None:
        with self.connection.cursor() as cur:
            cur.execute(
                """insert into ingest_manifest
                     (relpath, root, content_sha256, bytes, page_count, chunk_count,
                      failure_count, failure_kind, last_error, ingested_at, updated_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                   on conflict (relpath) do update set
                     root = excluded.root,
                     content_sha256 = excluded.content_sha256,
                     bytes = excluded.bytes,
                     page_count = excluded.page_count,
                     chunk_count = excluded.chunk_count,
                     failure_count = excluded.failure_count,
                     failure_kind = excluded.failure_kind,
                     last_error = excluded.last_error,
                     ingested_at = excluded.ingested_at,
                     updated_at = now()""",
                (relpath, record.get("root"), record.get("content_sha256"), record.get("bytes"),
                 record.get("page_count"), record.get("chunk_count", 0),
                 record.get("failure_count", 0), record.get("failure_kind"),
                 record.get("last_error")))

    def remove(self, relpath: str) -> None:
        with self.connection.cursor() as cur:
            cur.execute("delete from ingest_manifest where relpath = %s", (relpath,))

    def clear_failures(self, relpath: str | None = None) -> int:
        with self.connection.cursor() as cur:
            if relpath is None:
                cur.execute(
                    "update ingest_manifest set failure_count = 0, failure_kind = null, "
                    "last_error = null, updated_at = now() "
                    "where failure_count > 0 or failure_kind is not null")
            else:
                cur.execute(
                    "update ingest_manifest set failure_count = 0, failure_kind = null, "
                    "last_error = null, updated_at = now() where relpath = %s", (relpath,))
            return cur.rowcount

    def start_run(self) -> int | None:
        with self.connection.cursor() as cur:
            cur.execute("insert into ingest_runs default values returning id")
            return cur.fetchone()[0]

    def finish_run(self, run_id: int | None, counts: dict, note: str = "") -> None:
        if run_id is None:
            return
        with self.connection.cursor() as cur:
            cur.execute(
                """update ingest_runs set finished_at = now(), documents_seen = %s, documents_new = %s,
                     documents_changed = %s, documents_skipped = %s, documents_failed = %s,
                     chunks_written = %s, chunks_removed = %s, note = %s
                   where id = %s""",
                (counts.get("seen", 0), counts.get("new", 0), counts.get("changed", 0),
                 counts.get("skipped", 0), counts.get("failed", 0), counts.get("chunks_written", 0),
                 counts.get("chunks_removed", 0), note, run_id))


# --------------------------------------------------------------------------- a cycle

@dataclass
class CycleResult:
    seen: int = 0
    new: int = 0
    changed: int = 0
    skipped: int = 0
    failed: int = 0
    chunks_written: int = 0
    chunks_removed: int = 0
    documents_removed: int = 0
    failures: list[str] = field(default_factory=list)
    aborted: str | None = None

    def as_counts(self) -> dict:
        return {"seen": self.seen, "new": self.new, "changed": self.changed, "skipped": self.skipped,
                "failed": self.failed, "chunks_written": self.chunks_written,
                "chunks_removed": self.chunks_removed}


def ingest_cycle(services: Services, documents_root: Path, manifest: Manifest,
                 dry_run: bool = False) -> CycleResult:
    """One pass: ingest what is new or changed, skip what is not, and remove what has gone.

    The three decisions, in the order they are made.

    Whether to touch a document at all is decided by its bytes. The hash is taken before parsing, so an
    unchanged file costs one read and nothing else. This is the whole reason the manifest exists rather
    than a timestamp: a file that was touched but not edited should not cost a re-embed.

    Whether to replace a document's chunks is decided by the same hash, and the replacement is a delete
    then a write. Deleting first is what stops a document that shrank from leaving orphans behind, and it
    is safe because the write follows in the same cycle and a failure leaves the document marked failed
    rather than silent.

    Whether to remove a document entirely is decided from the manifest, not the filesystem. A document
    the manifest knows about whose file has gone is removed. A file that was never ingested cannot cause
    a removal, and a mount that failed to appear cannot empty a client's index.
    """
    result = CycleResult()
    present = discover(documents_root)
    result.seen = len(present)
    known = manifest.all()
    seen_relpaths = {document.relpath for document in present}

    # Removal first, so a document that was deleted does not stay retrievable while the rest of the cycle
    # runs. The alternative order would leave it citable for the duration.
    for relpath in sorted(set(known) - seen_relpaths):
        record = known[relpath]
        previous_chunks = record.get("chunk_count") or 0
        if not dry_run:
            delete_document(services, relpath)
            manifest.remove(relpath)
        result.documents_removed += 1
        result.chunks_removed += previous_chunks
        print(f"  removed {relpath}  ({previous_chunks} chunks)")

    for document in present:
        record = known.get(document.relpath)
        try:
            content_sha256, size = sha256_of(document.path)
        except OSError as exc:
            result.failed += 1
            result.failures.append(f"{document.relpath}: unreadable: {exc}")
            continue

        # The hash recorded for a FAILED document is the hash of the bytes that failed, so a dead-lettered
        # file stays dead until someone actually edits it. Recording the previous good hash instead was
        # the first version's bug: an unreadable file has no previous good hash, so it never matched and
        # was re-parsed every cycle forever, and its failure stopped being visible because it was always
        # there.
        failures = int((record or {}).get("failure_count") or 0)
        same_bytes = bool(record) and record.get("content_sha256") == content_sha256

        # A record claiming success with no chunks is not a document that has been ingested, it is a bug's
        # residue. The first real corpus run wrote two contract exhibits as zero chunks before the worker
        # learned to refuse them, and those rows say "ingested, unchanged, 0 chunks" ever since, so the
        # hash check skips them forever and the fix never reaches them. Treating zero chunks as never
        # ingested is the general form of that: the manifest is a cache of a claim about the index, and a
        # record with nothing behind it is a false claim whatever wrote it.
        if same_bytes and record and not (record.get("failure_count") or 0) \
                and not (record.get("chunk_count") or 0):
            same_bytes = False
            print(f"  {document.relpath} is recorded as ingested with no chunks behind it; "
                  f"re-ingesting")
        # The dead-letter limit counts only failures that are about the document. A transient one says
        # something about the moment, so the document is tried again on the next cycle: skipping it here
        # would mean it never recovers when the service comes back, which is the opposite of the point.
        # `failure_count` is not advanced for a transient failure, so this comparison cannot fire on one.
        if same_bytes and record and record.get("failure_kind") != "transient" \
                and failures >= MAX_FAILURES:
            result.skipped += 1
            result.failures.append(
                f"{document.relpath}: dead-lettered after {failures} failures, waiting for the file to change")
            continue
        if same_bytes and failures == 0 and record.get("failure_kind") is None:
            result.skipped += 1
            continue

        kind = "changed" if record else "new"
        if dry_run:
            result.new += 1 if not record else 0
            result.changed += 1 if record else 0
            print(f"  would ingest {document.relpath}  ({kind})")
            continue

        print(f"  ingesting {document.relpath}  ({kind}, {size} bytes)")
        try:
            points, pages = ingest_document(services, document, content_sha256)
            if record:
                delete_document(services, document.relpath)
            upsert_points(services, points)
        except Exception as exc:  # noqa: BLE001 - one bad document must not stop the cycle
            transient = is_transient(exc)
            result.failed += 1
            result.failures.append(
                f"{document.relpath}: {type(exc).__name__}: {exc}"
                + (" [transient, stays retryable]" if transient else ""))
            manifest.put(document.relpath, {
                "relpath": document.relpath, "root": document.root,
                # The hash of the bytes that FAILED, so the dead-letter check can match them. Keeping a
                # previous good hash here would mean a file that has never parsed correctly has no hash to
                # match against, and would be retried forever.
                "content_sha256": content_sha256,
                "bytes": size, "page_count": (record or {}).get("page_count"),
                "chunk_count": (record or {}).get("chunk_count") or 0,
                # The count only advances for failures that are about the document. A transient one
                # records itself so the next cycle knows to try again, and leaves the count alone.
                "failure_count": failures if transient else failures + 1,
                "failure_kind": "transient" if transient else "permanent",
                "last_error": f"{type(exc).__name__}: {exc}"[:500],
            })
            continue

        if record:
            result.changed += 1
        else:
            result.new += 1
        result.chunks_written += len(points)
        manifest.put(document.relpath, {
            "relpath": document.relpath, "root": document.root,
            "content_sha256": content_sha256, "bytes": size, "page_count": pages,
            "chunk_count": len(points), "failure_count": 0, "failure_kind": None, "last_error": None,
        })
        print(f"    {len(points)} chunks, {pages or '?'} pages")

    return result


# --------------------------------------------------------------------------- command line

def build_manifest(dsn: str | None) -> Manifest:
    """The database manifest when a DSN is configured and reachable, otherwise memory.

    Falling back rather than failing is deliberate. Refusing to ingest because a bookkeeping table is
    unreachable turns a records problem into an outage, and the cost of carrying on is a re-parse next
    cycle. The run says which one it used, so a fallback is visible rather than silent.
    """
    if not dsn:
        print("no DATABASE_URL set, so this cycle keeps its manifest in memory: every document will "
              "look new and be re-ingested")
        return MemoryManifest()
    try:
        return PostgresManifest(dsn)
    except Exception as exc:  # noqa: BLE001
        print(f"could not reach the database for the manifest ({exc}); carrying on with an in-memory "
              f"manifest, which means documents will be re-ingested rather than skipped")
        return MemoryManifest()


def record_cycle(result: CycleResult) -> None:
    """Write the cycle's outcome where something other than a log can read it.

    A worker with no listening socket cannot be asked whether it is well. The alternative to recording
    the answer is a healthcheck that reports the process is alive, which stays green while every document
    fails, so this writes what happened instead.
    """
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "counts": result.as_counts(),
            "documents_removed": result.documents_removed,
            "failures": result.failures,
        }, indent=2) + "\n")
    except OSError as exc:  # noqa: BLE001 - a records problem must not fail an ingestion
        print(f"could not write {STATE_PATH}: {exc}")


def cmd_status(state_path: Path) -> int:
    """Exit 0 when the last cycle finished with no failures. This is what the healthcheck runs."""
    if not state_path.exists():
        print(f"no cycle has run yet ({state_path} does not exist)")
        return 1
    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {state_path}: {exc}")
        return 1
    counts = state.get("counts") or {}
    failed = int(counts.get("failed") or 0)
    print(f"last cycle at {state.get('finished_at')}: {json.dumps(counts)}")
    if failed:
        print(f"unhealthy: {failed} document(s) failed")
        for failure in state.get("failures") or []:
            print(f"  {failure}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Ingest documents into the vector store.",
        epilog="Exits non-zero when a document failed, so a scheduled run surfaces a bad file rather "
               "than reporting success because the cycle completed.")
    parser.add_argument("--documents", default=os.environ.get("DOCUMENTS_ROOT", "/documents"),
                        help="the directory tree to ingest")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen and write nothing")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.add_argument("--once", action="store_true",
                        help="accepted for symmetry with the scheduled form; a single cycle is the default")
    parser.add_argument("--status", action="store_true",
                        help="report the last cycle's outcome and exit; this is the healthcheck")
    parser.add_argument("--clear-failures", metavar="RELPATH", nargs="?", const="",
                        help="forget recorded failures so dead-lettered documents are tried again. "
                             "With no argument, all of them. Use after a failure rule changes, because "
                             "the rows written under the old rule cannot know it did.")
    parser.add_argument("--state", default=str(STATE_PATH),
                        help="where the last cycle's result is recorded")
    args = parser.parse_args(argv)

    if args.status:
        return cmd_status(Path(args.state))

    if args.clear_failures is not None:
        # Dry runs have no manifest to repair, and saying so beats silently reporting zero.
        if args.dry_run:
            print("--clear-failures needs the real manifest, not a dry run")
            return 1
        repair_manifest = build_manifest(os.environ.get("DATABASE_URL"))
        target = args.clear_failures or None
        cleared = repair_manifest.clear_failures(target)
        print(f"cleared failures on {cleared} document(s)"
              + (f" matching {target!r}" if target else "")
              + ". The next cycle will try them again.")
        return 0

    services = Services()
    manifest = MemoryManifest() if args.dry_run else build_manifest(os.environ.get("DATABASE_URL"))
    documents_root = Path(args.documents)
    if not documents_root.exists():
        print(f"no documents directory at {documents_root}")
        return 1

    # Before the first document, so a missing or wrong-sized collection is one clear failure at the start
    # of a cycle rather than one identical error per document.
    try:
        ensure_collection(services)
    except Exception as exc:  # noqa: BLE001
        print(f"cannot use the vector store: {exc}")
        return 1

    run_id = None if args.dry_run else manifest.start_run()
    result = ingest_cycle(services, documents_root, manifest, dry_run=args.dry_run)
    if not args.dry_run:
        manifest.finish_run(run_id, result.as_counts())
        record_cycle(result)

    if args.json:
        print(json.dumps({"seen": result.seen, "new": result.new, "changed": result.changed,
                          "skipped": result.skipped, "failed": result.failed,
                          "chunks_written": result.chunks_written,
                          "chunks_removed": result.chunks_removed,
                          "documents_removed": result.documents_removed,
                          "failures": result.failures}, indent=2))
    else:
        print()
        print(f"seen {result.seen}, new {result.new}, changed {result.changed}, "
              f"skipped {result.skipped}, failed {result.failed}")
        print(f"chunks written {result.chunks_written}, chunks removed {result.chunks_removed}, "
              f"documents removed {result.documents_removed}")
        for failure in result.failures:
            print(f"  failed: {failure}")

    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
