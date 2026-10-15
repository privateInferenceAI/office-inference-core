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

# The same extractor the retriever queries with, in this directory so the image's fixed file list carries it.
# Sharing one implementation is the point: `W-4` is `w4` at ingestion and `w4` in a query because it is one
# function, not two that agree today.
from identifiers import extract as identify  # noqa: E402

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

# Bumped when the MEANING of a stored payload changes, which is a different thing from its content.
#
# The skip decision is made on the file's bytes, and that is right for a file that was edited. It is wrong
# for a change to the code that derives the payload from those bytes, because then the bytes are identical,
# the manifest still believes its old claim, and the new logic never reaches the documents it was written
# for. That has happened twice in this step: once for how empty documents are refused, and once for is_ocr,
# where all 29 chunks of the two HTML exhibits stayed labelled machine-read after the code that labelled
# them was corrected.
#
# Bumping this makes every document look stale once, so the corpus is re-derived and the index and the
# manifest agree with the code that is running. It is deliberately separate from the chunker's version: a
# payload change does not change a chunk's text or its id, and forcing a re-parse for a labelling fix would
# hide the difference between the two.
#
# **Bump it when a new store is added, not only when a field changes.** Step 3.9 added the lexical index and
# did not bump this, so the cycle skipped all four documents, the new write path never ran, and the acceptance
# probe found an empty chunk_index with no error anywhere. Nothing was wrong with the code: the corpus had
# simply been ingested under rules that did not include the new store. v3 is that bump.
PAYLOAD_VERSION = "payload-v4"

# File types the pipeline accepts. Anything else is ignored rather than failed, because a client's
# document folder will contain a .DS_Store and a shortcut sooner or later, and neither is an error.
# What the worker will look at. `.eml` was missing until RET-FORMAT-001, so a client's saved email messages were
# skipped as an unsupported format while the parser handled them perfectly: the requirement names email among
# the formats that must work, and the only thing standing between it working and not was this set.
#
# `.msg` is deliberately NOT here. It is a compound binary format rather than RFC 822, and whether the parser
# reads it is unverified. Adding a suffix to this set is a promise that files with it are ingested, and an
# unverified promise is worse than an honest gap: a client would see the file in their folder and not in the
# index with nothing said.
ACCEPTED_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".markdown",
                     ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp", ".eml"}


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
    # The lexical arm lives in Postgres. Absent means the arm is skipped rather than fatal, so a deployment
    # without the chunk index still answers from vectors.
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    timeout: int = 900
    # One document's parse budget. Generous on purpose: a 59-page scan through OCR is minutes of CPU on
    # this box, and the alternative to waiting is failing.
    parse_timeout: float = float(os.environ.get("INGEST_PARSE_TIMEOUT", "3600"))
    # Discovered once per run, because the embedding service has had more than one batch endpoint and
    # asking which one answers is cheaper than being wrong about it on every chunk.
    embed_path: str | None = None
    # The preprocessing half of the embedding contract. These change what a stored vector means as surely as the
    # model does, and they are ours rather than the server's, so they belong in the recorded contract. Read from
    # the environment because a manifest decision with a schema and a diff is how the rest of this system
    # carries a decision; the defaults are empty because a prefix nobody set is no prefix.
    query_prefix: str = field(default_factory=lambda: os.environ.get("EMBED_QUERY_PREFIX", ""))
    passage_prefix: str = field(default_factory=lambda: os.environ.get("EMBED_PASSAGE_PREFIX", ""))
    normalise_embeddings: bool = field(
        default_factory=lambda: (os.environ.get("EMBED_NORMALISE", "true").lower() != "false"))
    # Measured once and remembered, so the contract can carry the size without calling the service on every
    # comparison.
    dimension: int | None = None


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


def _get_json(url: str, timeout: int) -> dict | None:
    """A GET that returns a JSON object, or None when the service says it has nothing yet."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw) if raw else {}
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


# The conversion options, and every one of them is a real option of the service this talks to.
#
# Three of the four in an earlier version were not. `do_ocr`, `do_table_structure` and `table_mode` are
# ignored by docling-serve 1.36, which takes `ocr_preset` and `table_structure_preset`, and it ignores an
# unknown field while answering success. That cost three round trips, so the service this now talks to is
# `services/parser`, which REFUSES an option it does not understand and names the ones it accepts.
#
# `ocr=True` runs OCR only where there is no text layer, which is what the released default did. A 59-page
# government PDF has a text layer, so forcing OCR on it would rasterise every page and read back text that
# was already there: the most expensive thing this box can be asked to do, for no gain.
PARSE_OPTIONS = {
    "to_formats": "json",
    "image_export_mode": "placeholder",
    "ocr": "true",
    "table_structure": "true",
}

# How long to wait for one document, and how often to ask. The synchronous endpoint gives up after
# DOCLING_SERVE_MAX_SYNC_WAIT, which defaults to 120 seconds, and the handler carries `# TODO: abort task!`
# so the parse keeps running after the client is told it failed. That combination is why one 59-page PDF
# produced a 504 on every attempt while a docling process accumulated over six hours of CPU time: each
# retry orphaned a parse and started another. The asynchronous endpoint has no such limit and reports
# progress, so the wait is this worker's to choose.
PARSE_POLL_SECONDS = 10


def parse_document(services: Services, path: Path, relpath: str) -> dict:
    """The parser's structured output for one file, submitted and then waited for.

    Asynchronous by necessity rather than preference. The synchronous endpoint's wait is capped at 120
    seconds by the service, and it does not stop the work when it gives up, so a slow document fails every
    time and leaves a parse behind on each attempt. Submitting a task and polling it separates the two
    questions: whether the parser accepted the work, which is answerable in a second, and how long the work
    takes, which is the worker's budget rather than the service's.
    """
    accepted = _with_retries(
        f"submitting {relpath}",
        lambda: _multipart(
            f"{services.parser_url}/v1/convert/file/async", path.name, path.read_bytes(),
            PARSE_OPTIONS, services.timeout))
    task_id = accepted.get("task_id")
    if not task_id:
        raise RuntimeError(
            f"the parser accepted {relpath} without returning a task id: {accepted!r}")

    deadline = time.monotonic() + services.parse_timeout
    while time.monotonic() < deadline:
        status = _get_json(
            f"{services.parser_url}/v1/status/poll/{task_id}?wait={PARSE_POLL_SECONDS}",
            services.timeout)
        state = (status or {}).get("task_status")
        if state == "success":
            break
        if state in ("failure", "skipped"):
            raise RuntimeError(
                f"the parser failed on {relpath}: {json.dumps(status)[:400]}")
        time.sleep(PARSE_POLL_SECONDS)
    else:
        raise TimeoutError(
            f"the parser did not finish {relpath} within {services.parse_timeout}s. The task is still "
            f"running on the box as {task_id}; raising rather than resubmitting is what keeps one slow "
            f"document from becoming several.")

    payload = _get_json(f"{services.parser_url}/v1/result/{task_id}", services.timeout) or {}
    document = (payload.get("document") or {}).get("json_content") or {}
    if not document:
        raise RuntimeError(
            f"the parser returned no structured output for {relpath}. Its status was "
            f"{payload.get('status')!r}; an empty json_content usually means the options were not "
            f"accepted, and the real option names come back in the parser's own 422, which lists every "
            f"field it accepts.")
    # Both confidence reports travel with the document, because the chunker cannot see them and the worker
    # needs them to decide whether a figure was recognised from an image. `confidence` is the document
    # rollup and `confidence_pages` is the per-page map, which is the one that can name a scanned page:
    # the rollup cannot, because a 5th-percentile average over 59 pages is not moved by one bad page.
    #
    # `confidence_pages` is absent from docling-serve, so this field is what distinguishes the two
    # services. Against docling-serve the worker falls back to the document-level answer, which is what it
    # did before services/parser existed.
    return {
        "document": document,
        "confidence": payload.get("confidence") or {},
        "confidence_pages": payload.get("confidence_pages") or {},
    }


def ocr_pages(confidence_pages: dict | None) -> set[int]:
    """The pages OCR actually ran on, from the per-page confidence report.

    A page whose `ocr_score` is null is a page OCR did not touch, and a page that has one was read off an
    image. Measured on the fixture corpus's 59-page publication: pages 1 and 8 carry a score and the other
    57 do not, which matches the in-process report exactly.

    This is the fact the pipeline could not have before. docling-serve computes it and serialises only the
    document-level rollup, so the label had to be guessed from the document as a whole and a mixed filing
    came out labelled entirely one way or the other.
    """
    if not confidence_pages:
        return set()
    pages: set[int] = set()
    for key, page in confidence_pages.items():
        if not isinstance(page, dict):
            continue
        if page.get("ocr_score") is not None:
            try:
                pages.add(int(key))
            except (TypeError, ValueError):
                continue
    return pages


def _ocr_ran(parsed: dict, confidence: dict | None = None) -> bool:
    """Whether the parser read this document with OCR rather than from a digital text layer.

    Decided from the parser's own confidence report, because the first version of this looked for an
    `origin` field on each item's provenance and this release does not emit one. Provenance carries
    page_no, bbox and charspan and nothing else, so the search never matched, the fallback answered True
    every time, and an HTML contract was labelled as machine-read text.

    The signal that does exist is `parse_score`, which is the tenth percentile of DIGITAL text cells. It is
    null when the document has no digital text at all, which is exactly the scanned case, and it is a
    number when there is a text layer to have taken a percentile of. Measured on this box:

        scanned fixture, no text layer    parse_score null, ocr_score 0.989
        IRS publication, text layer       parse_score 1.0,  ocr_score 0.828

    Where there is no confidence report the answer is True. That is the safe direction: it labels a figure
    as machine-read when it might not have been, rather than presenting a machine's guess as text that was
    already there.
    """
    report = confidence if confidence is not None else (parsed.get("confidence") or {})
    if not report:
        return True

    # "Unspecified" means the parser scored nothing, which is what it returns for a format it does not run
    # the scoring stages over: HTML, Markdown, plain text. The report is not empty in that case, it is a
    # full set of nulls with both grades set to "unspecified", and testing only for emptiness let one of
    # those through to the parse_score branch below. Measured on the corpus's two HTML contract exhibits:
    #
    #     {"parse_score": null, ..., "mean_grade": "unspecified", "low_grade": "unspecified"}
    #
    # A document with no digital text cells is a scanned document. A document the scorer did not look at is
    # a format with no pages, and calling that OCR presents text as machine-read when nothing read it.
    grade = str(report.get("mean_grade") or "").lower()
    scores = [report.get(name) for name in
              ("parse_score", "layout_score", "table_score", "ocr_score", "mean_score", "low_score")]
    if grade == "unspecified" or all(score is None for score in scores):
        return False

    if report.get("parse_score") is None:
        # No digital text cells, and the scorer did run, so every character came from a recognition step.
        return True
    # A text layer exists. It may still have been OCR'd, because ocr_score is present on both documents,
    # but a document with digital text is not a document whose figures were read off an image, and the flag
    # is a statement a reader acts on.
    return False


def _contract_change_allowed() -> bool:
    """Whether a contract change is deliberate. Never by default.

    A check that repairs itself on noticing a mismatch re-embeds a client's corpus at whatever moment the
    mismatch is noticed, which could be during a search. The opt-in is an environment variable rather than a
    flag on the cycle so that running the cycle the ordinary way can never be the act that changes it.
    """
    return (os.environ.get("EMBED_CONTRACT_ALLOW_CHANGE", "").strip().lower() in ("1", "true", "yes"))


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
                payload = _post(f"{services.embed_url}{path}",
                                {"inputs": [f"{services.passage_prefix}{text}" for text in texts]},
                                services.timeout)
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
        services.dimension = measured
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
    services.dimension = size
    return measured


def upsert_points(services: Services, points: list[dict]) -> None:
    if not points:
        return
    _post(f"{services.vector_url}/collections/{services.collection}/points?wait=true",
          {"points": points}, services.timeout, method="PUT")


# The share of the smaller document's chunks that two documents have in common, above which the pair is
# flagged for a human. Chosen rather than tuned, and the choice is explained in the runbook: too low and every
# pair of documents in a series is flagged, too high and a genuine second copy slips through because one page
# differs. Half is the point where "these are substantially the same document" stops being a judgement.
DUPLICATE_OVERLAP_THRESHOLD = 0.5

# How much identical text makes two documents the same document rather than two documents that share a line.
#
# Measured in characters rather than chunks, and the first version got this wrong in a way that suppressed the
# most likely duplicates. A floor of five chunks meant a one-page fee schedule exported twice was never
# compared, and single-page documents are exactly what a small business duplicates: a policy, a schedule, a
# price list. What the floor is for is boilerplate, and a shared cover line is not evidence while a shared page
# is, so the threshold is on the amount of identical text.
#
# Five hundred characters is roughly a page of a fee schedule or half a page of prose. Below that two documents
# agreeing is a coincidence worth ignoring; above it, somebody should look.
DUPLICATE_MIN_SHARED_CHARS = 500


def index_for_lexical(manifest: "Manifest", points: list[dict]) -> None:
    """Write the lexical arm's copy of these chunks, beside the vectors.

    A second store for the same content, which is worth stating plainly because it is a real cost: every
    chunk is now written twice and can be read from two places. The reason is in `010_chunk_index.sql`: the
    vector arm cannot see a rare reference, and the measurement that opened this step showed seven identifier
    questions where it missed two entirely and plain full-text missed all seven.

    The access label travels with the row, because the lexical arm has to filter as well. A second path to
    the same content with no filter on it is the leak that step 3.8 closed, arriving by a new road.

    Written through the manifest's own connection rather than a new one, so the two writes share a
    transaction boundary and a failure leaves neither.
    """
    manifest.index_chunks([
        {
            "chunk_id": point["id"],
            "doc_id": point["payload"].get("doc_id"),
            "acl": point["payload"].get("acl"),
            "text": point["payload"].get("text"),
            "page_start": point["payload"].get("page_start"),
            "page_end": point["payload"].get("page_end"),
            "kind": point["payload"].get("kind"),
            "chunker_version": point["payload"].get("chunker_version"),
            "source_sha256": point["payload"].get("source_sha256"),
            "identifiers": identify(point["payload"].get("text") or ""),
        }
        for point in points
    ])


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


@dataclass
class PhaseTimings:
    """Where one document's time went, in milliseconds, by phase.

    **Why this exists.** Two attempts to say what a slow ingest is waiting on have been wrong, both times
    because the measurement was taken while the ingest was running and therefore measured a queue rather
    than a cost. The worker recorded nothing per phase, so there was no way to answer the question from a
    real run at all: only the end-to-end rate was visible.

    **Four phases, and they are the ones a decision turns on.** `parse_ms` is the parser round trip, which
    for a scanned PDF or an OCR page is where a corpus's time goes. `chunk_ms` is local CPU and should be
    near zero, and is here to prove that rather than assume it. `embed_ms` is the embedding service, which
    is the phase a GPU would remove. `write_ms` is the vector store and the lexical table, which is the
    phase that parallelising across documents would overlap.

    **Recorded always, printed in the cycle summary, and never persisted.** The manifest's job is to say
    what has been ingested and whether it worked; timing is a property of the machine and the moment, and a
    column that changed every run would make the manifest useless for the diff it exists to make.
    """

    parse_ms: float = 0.0
    chunk_ms: float = 0.0
    embed_ms: float = 0.0
    write_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.parse_ms + self.chunk_ms + self.embed_ms + self.write_ms

    def add(self, other: "PhaseTimings") -> None:
        self.parse_ms += other.parse_ms
        self.chunk_ms += other.chunk_ms
        self.embed_ms += other.embed_ms
        self.write_ms += other.write_ms

    def shares(self) -> dict[str, float]:
        """Each phase as a fraction of the total, for a summary line. Empty when nothing was timed."""
        total = self.total_ms
        if total <= 0:
            return {}
        return {name: getattr(self, name) / total
                for name in ("parse_ms", "chunk_ms", "embed_ms", "write_ms")}


# The phases, in the order a document goes through them, with the name a summary line uses.
PHASES = (("parse_ms", "parse"), ("chunk_ms", "chunk"), ("embed_ms", "embed"), ("write_ms", "write"))


def timings_line(label: str, t: PhaseTimings, chunks: int) -> str:
    """One document's split, in a form a person can read next to the line that says what it is doing."""
    return (f"    {label}: parse {t.parse_ms:.0f}ms, chunk {t.chunk_ms:.0f}ms, "
            f"embed {t.embed_ms:.0f}ms, write {t.write_ms:.0f}ms  -> total {t.total_ms:.0f}ms "
            f"for {chunks} chunk(s)")


def ingest_document(services: Services, document: Document, content_sha256: str,
                    page_count: int | None = None,
                    timings: "PhaseTimings | None" = None) -> tuple[list[dict], int]:
    """One document into points, and the number of pages the parser saw.

    The chunk ids come from `chunker`, which derives them from the document id, the content hash, the
    chunker version and the chunk's own hash. So the same bytes produce the same ids, a changed file
    produces new ones, and a changed chunker produces new ones again. That is what makes a re-run an
    update rather than a duplication.
    """
    # **Timed here rather than around the call site**, because two of the four phases are inside this
    # function and splitting the measurement across two places is how the two halves drift.
    _t0 = time.monotonic()
    parsed_result = parse_document(services, document.path, document.relpath)
    _t1 = time.monotonic()
    parsed = parsed_result["document"]
    chunks = chunker.chunk_document(parsed, doc_id=document.relpath, source_sha256=content_sha256)
    _t2 = time.monotonic()
    pages = len(parsed.get("pages") or {}) or None
    if timings is not None:
        timings.parse_ms += (_t1 - _t0) * 1000
        timings.chunk_ms += (_t2 - _t1) * 1000

    # Per chunk rather than per document. A chunk that overlaps a page OCR read carries text that was
    # recognised from an image, and one that does not, does not. The document-level answer was a single
    # label for a mixed filing, which is the version that misleads a reader.
    scanned = ocr_pages(parsed_result.get("confidence_pages"))
    document_ocr = _ocr_ran(parsed, parsed_result.get("confidence"))
    # The parser's confidence, which the citation verifier's confidence gate needs and which was previously
    # read for one boolean and discarded. It is DOCUMENT-level, and that is a bound rather than a choice:
    # docling-serve does not populate the per-page confidence map this release exposes, so a page whose digits
    # were misread cannot be told from one read cleanly. The gate is coarser than the architecture asks for.
    confidence_score = (parsed_result.get("confidence") or {}).get("parse_score")

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

    points: list[dict] = []
    _embed_started = time.monotonic()
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
            if scanned:
                payload["is_ocr"] = any(
                    page in scanned for page in range(chunk.page_start, chunk.page_end + 1))
            else:
                # No per-page report, which happens for a format the parser does not score. Fall back to
                # the document-level answer, which is what this field used to be.
                payload["is_ocr"] = document_ocr
            # How many of this chunk's pages were read off an image, so a reader can be told the figure is
            # partly machine-read rather than being given a yes or no about the whole passage.
            payload["ocr_page_count"] = sum(
                1 for page in range(chunk.page_start, chunk.page_end + 1) if page in scanned)
            # The parser's own confidence in what it read, which the confidence gate needs and which was
            # being discarded. It is DOCUMENT-level and not page-level, and that is a real bound rather than
            # an implementation choice: docling-serve does not populate the per-page confidence map this
            # release exposes, so a page whose digits were misread cannot be distinguished from one that was
            # read cleanly. The gate is therefore coarser than the architecture asks for, and it is recorded
            # in the runbook as a bound rather than presented as the designed check.
            payload["parse_score"] = confidence_score
            points.append({"id": chunk.chunk_id, "vector": vector, "payload": payload})
    if timings is not None:
        timings.embed_ms += (time.monotonic() - _embed_started) * 1000
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

    def index_chunks(self, rows: list[dict]) -> None:
        """Write the lexical arm's rows. A no-op in a manifest that has no database behind it."""
        return None

    def near_duplicates(self, doc_id: str, threshold: float) -> list[tuple[str, float]]:
        """Other documents sharing at least `threshold` of this one's chunks. Empty without a database."""
        return []

    def all_document_ids(self) -> list[str]:
        """Every document the lexical index holds, for a scan that is not driven by a file arriving."""
        return []

    def set_current(self, doc_id: str, is_current: bool, superseded_by: str | None = None,
                    note: str | None = None) -> None:
        """Declare whether a document may be relied on. A human's decision, never inferred."""
        return None

    def current_documents(self) -> dict[str, bool]:
        """doc_id to is_current, for the documents that have a declaration."""
        return {}

    def failed_documents(self) -> list[dict]:
        """Nothing is recorded in memory, so nothing has failed. The Postgres manifest is the one that
        remembers, and `--failures` reads it."""
        return []

    def unresolved_duplicates(self) -> list[dict]:
        """Near-duplicate pairs nobody has resolved yet."""
        return []

    def remove_chunks(self, doc_id: str) -> None:
        """Remove a document's rows from the lexical arm."""
        return None

    def start_run(self) -> int | None:
        return None

    def finish_run(self, run_id: int | None, counts: dict, note: str = "") -> None:
        return None


class MemoryManifest(Manifest):
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        # Hash of each chunk's TEXT per document, which is the signature duplicate detection compares, and
        # the declared currency of each document. A test supplies these rather than deriving them, because
        # deriving them is what the database does.
        # doc_id -> [(md5 of the chunk's text, its length in characters)], which is what the comparison
        # needs: identity for the overlap and length for the floor on how much text has to be identical.
        self.chunk_hashes: dict[str, list[tuple[str, int]]] = {}
        self.versions: dict[str, bool] = {}

    def get(self, relpath: str) -> dict | None:
        return self.records.get(relpath)

    def all(self) -> dict[str, dict]:
        return dict(self.records)

    def put(self, relpath: str, record: dict) -> None:
        self.records[relpath] = record

    def remove(self, relpath: str) -> None:
        self.records.pop(relpath, None)

    def all_document_ids(self) -> list[str]:
        return sorted(self.chunk_hashes)

    def near_duplicates(self, doc_id: str, threshold: float) -> list[tuple[str, float]]:
        """The same comparison the database does, over a hash of each chunk's TEXT.

        A test supplies text hashes rather than chunk ids, because a chunk id carries the document it belongs
        to and comparing ids can never match two copies of the same file. Supplying ids by hand is how the
        first version of this test passed while the query it was testing was broken.
        """
        # The memory form takes (hash, characters) pairs so the same floor applies here as in the query.
        mine = dict(self.chunk_hashes.get(doc_id, []))
        if not mine:
            return []
        found = []
        for other, theirs_list in sorted(self.chunk_hashes.items()):
            if other == doc_id:
                continue
            theirs = dict(theirs_list)
            shared = set(mine) & set(theirs)
            shared_chars = sum(mine[sig] for sig in shared)
            smaller = min(len(mine), len(theirs))
            if smaller and shared_chars >= DUPLICATE_MIN_SHARED_CHARS \
                    and len(shared) / smaller >= threshold:
                found.append((other, len(shared) / smaller))
        return found

    def set_current(self, doc_id: str, is_current: bool, superseded_by: str | None = None,
                    note: str | None = None) -> None:
        self.versions[doc_id] = is_current

    def current_documents(self) -> dict[str, bool]:
        return dict(self.versions)

    def failed_documents(self) -> list[dict]:
        """Nothing is recorded in memory, so nothing has failed. The Postgres manifest is the one that
        remembers, and `--failures` reads it."""
        return []

    def unresolved_duplicates(self) -> list[dict]:
        return []

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
                "failure_count, failure_kind, last_error, ingested_at, payload_version "
                "from ingest_manifest where relpath = %s",
                (relpath,))
            row = cur.fetchone()
        if not row:
            return None
        keys = ("relpath", "root", "content_sha256", "bytes", "page_count", "chunk_count",
                "failure_count", "failure_kind", "last_error", "ingested_at", "payload_version")
        return dict(zip(keys, row))

    def all(self) -> dict[str, dict]:
        with self.connection.cursor() as cur:
            cur.execute("select relpath, root, content_sha256, chunk_count, failure_count, "
                        "failure_kind, last_error, payload_version from ingest_manifest")
            rows = cur.fetchall()
        keys = ("relpath", "root", "content_sha256", "chunk_count", "failure_count", "failure_kind",
                "last_error", "payload_version")
        return {row[0]: dict(zip(keys, row)) for row in rows}

    def put(self, relpath: str, record: dict) -> None:
        with self.connection.cursor() as cur:
            cur.execute(
                """insert into ingest_manifest
                     (relpath, root, content_sha256, bytes, page_count, chunk_count,
                      failure_count, failure_kind, last_error, payload_version, ingested_at, updated_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                   on conflict (relpath) do update set
                     root = excluded.root,
                     content_sha256 = excluded.content_sha256,
                     bytes = excluded.bytes,
                     page_count = excluded.page_count,
                     chunk_count = excluded.chunk_count,
                     failure_count = excluded.failure_count,
                     failure_kind = excluded.failure_kind,
                     last_error = excluded.last_error,
                     payload_version = excluded.payload_version,
                     ingested_at = excluded.ingested_at,
                     updated_at = now()""",
                (relpath, record.get("root"), record.get("content_sha256"), record.get("bytes"),
                 record.get("page_count"), record.get("chunk_count", 0),
                 record.get("failure_count", 0), record.get("failure_kind"),
                 record.get("last_error"), record.get("payload_version")))

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

    def index_chunks(self, rows: list[dict]) -> None:
        """Upsert the lexical rows, replacing on conflict so a re-derivation corrects a row in place."""
        if not rows:
            return
        from psycopg2.extras import execute_values
        with self.connection.cursor() as cur:
            execute_values(cur, """
                insert into chunk_index
                  (chunk_id, doc_id, acl, text, page_start, page_end, kind, chunker_version,
                   source_sha256, identifiers)
                values %s
                on conflict (chunk_id) do update set
                  doc_id = excluded.doc_id,
                  acl = excluded.acl,
                  text = excluded.text,
                  page_start = excluded.page_start,
                  page_end = excluded.page_end,
                  kind = excluded.kind,
                  chunker_version = excluded.chunker_version,
                  source_sha256 = excluded.source_sha256,
                  identifiers = excluded.identifiers
            """, [(row["chunk_id"], row["doc_id"], row["acl"], row["text"], row["page_start"],
                   row["page_end"], row["kind"], row["chunker_version"], row["source_sha256"],
                   row["identifiers"]) for row in rows])

    def remove_chunks(self, doc_id: str) -> None:
        with self.connection.cursor() as cur:
            cur.execute("delete from chunk_index where doc_id = %s", (doc_id,))

    def all_document_ids(self) -> list[str]:
        with self.connection.cursor() as cur:
            cur.execute("select distinct doc_id from chunk_index order by doc_id")
            return [row[0] for row in cur.fetchall()]

    def near_duplicates(self, doc_id: str, threshold: float) -> list[tuple[str, float]]:
        """Documents sharing at least `threshold` of this one's chunks.

        The signature is a hash of each chunk's TEXT, not its id, and that distinction is the whole correctness
        of this function. A chunk id is `uuid5(doc_id, source_sha256, chunker_version, ordinal, content_hash)`,
        so it contains the document it belongs to: two byte-identical files produce two documents whose chunk
        ids all differ, an overlap computed over ids is always zero, and the feature never fires. The first
        version compared ids and reported no duplicates for a corpus containing an exact copy, while its unit
        test passed because the test supplied shared ids by hand.

        Hashing the text in the query costs a little and needs no schema change, because `chunk_index` already
        holds the text. The share is over the SMALLER document so a two-page extract of a long report reads as
        a small overlap rather than a complete one, and both sides need a handful of chunks so two small
        documents that happen to agree are not reported as duplicates of each other.
        """
        with self.connection.cursor() as cur:
            cur.execute("""
                with mine as (
                  select md5(text) as sig, length(text) as chars
                  from chunk_index where doc_id = %(doc)s
                ),
                theirs as (
                  select doc_id, md5(text) as sig, length(text) as chars
                  from chunk_index where doc_id <> %(doc)s
                ),
                shared as (
                  select t.doc_id,
                         count(*) as common,
                         sum(t.chars) as shared_chars,
                         (select count(*) from mine) as mine_count,
                         (select count(*) from theirs x where x.doc_id = t.doc_id) as their_count
                  from theirs t
                  where t.sig in (select sig from mine)
                  group by t.doc_id
                )
                select doc_id, common::float / least(mine_count, their_count)
                from shared
                where shared_chars >= %(floor_chars)s
                  and common::float / least(mine_count, their_count) >= %(threshold)s
                order by doc_id
            """, {"doc": doc_id, "threshold": threshold,
                  "floor_chars": DUPLICATE_MIN_SHARED_CHARS})
            found = [(row[0], float(row[1])) for row in cur.fetchall()]

            for other, overlap in found:
                # Ordered so a pair is one row whichever document arrived second, which is what makes the
                # uniqueness constraint mean what it says rather than allowing both orders.
                first, second = sorted((doc_id, other))
                cur.execute("""
                    insert into duplicate_flags (doc_id, other_doc_id, chunk_overlap)
                    values (%s, %s, %s)
                    on conflict (doc_id, other_doc_id) do update set chunk_overlap = excluded.chunk_overlap
                """, (first, second, overlap))
            return found

    def set_current(self, doc_id: str, is_current: bool, superseded_by: str | None = None,
                    note: str | None = None) -> None:
        with self.connection.cursor() as cur:
            cur.execute("""
                insert into document_versions (doc_id, is_current, superseded_by, superseded_at, note,
                                               updated_at)
                values (%s, %s, %s, case when %s then null else now() end, %s, now())
                on conflict (doc_id) do update set
                  is_current = excluded.is_current,
                  superseded_by = excluded.superseded_by,
                  superseded_at = excluded.superseded_at,
                  note = excluded.note,
                  updated_at = now()
            """, (doc_id, is_current, superseded_by, is_current, note))
            # Resolving one side of a pair settles the flag, because somebody has now looked at it.
            cur.execute("""
                update duplicate_flags set resolved_at = now(), resolution = %s
                where resolved_at is null and (doc_id = %s or other_doc_id = %s)
            """, (note or ("current" if is_current else "superseded"), doc_id, doc_id))

    def current_documents(self) -> dict[str, bool]:
        with self.connection.cursor() as cur:
            cur.execute("select doc_id, is_current from document_versions")
            return {row[0]: row[1] for row in cur.fetchall()}

    def failed_documents(self) -> list[dict]:
        """Every document with a recorded failure, the reason, and whether it is still being retried.

        **This reads the MANIFEST rather than the last cycle's record, and that is the whole point.**
        `--status` reports what failed in the cycle that just ran, so a document that has been dead-lettered
        drops out of it: the next cycle skips the file, succeeds, and the deployment reports healthy while a
        client's document is silently not in the index. The manifest is the only place that remembers, and
        until this method nothing read it.
        """
        with self.connection.cursor() as cur:
            cur.execute("""
                select relpath, failure_count, failure_kind, last_error
                from ingest_manifest
                where coalesce(failure_count, 0) > 0 or failure_kind is not null
                order by relpath""")
            return [{"relpath": row[0], "failure_count": row[1], "failure_kind": row[2],
                     "last_error": row[3]} for row in cur.fetchall()]

    def unresolved_duplicates(self) -> list[dict]:
        with self.connection.cursor() as cur:
            cur.execute("""select doc_id, other_doc_id, chunk_overlap, detected_at
                           from duplicate_flags where resolved_at is null
                           order by chunk_overlap desc, doc_id""")
            return [{"doc_id": row[0], "other_doc_id": row[1], "overlap": float(row[2]),
                     "detected_at": row[3]} for row in cur.fetchall()]

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
    # Near-duplicate pairs found this cycle, for the summary. Recorded rather than acted on.
    duplicates: list[tuple[str, str, float]] = field(default_factory=list)
    # **Where the cycle's time went, summed over the documents it ingested.** Not persisted: timing is a
    # property of the machine and the moment, and a manifest column that changed every run would defeat the
    # diff the manifest exists to make.
    timings: "PhaseTimings" = field(default_factory=PhaseTimings)
    timed_documents: int = 0

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
            # Both stores, or a removed document stays retrievable through the arm nobody cleared.
            manifest.remove_chunks(relpath)
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

        # Same bytes, older payload rules. The document is re-derived from its parse rather than trusted,
        # because what changed is how its labels are computed and the stored ones were computed the old way.
        if same_bytes and (record.get("payload_version") or "") != PAYLOAD_VERSION:
            print(f"  {document.relpath} was ingested under payload rules "
                  f"{record.get('payload_version') or 'recorded before this field existed'} and this "
                  f"worker writes {PAYLOAD_VERSION}; re-deriving it")
            same_bytes = False

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
        timings = PhaseTimings()
        try:
            points, pages = ingest_document(services, document, content_sha256, timings=timings)
            _write_started = time.monotonic()
            if record:
                delete_document(services, document.relpath)
            upsert_points(services, points)
            # The lexical arm's copy, written after the vectors so a failure part way leaves the vectors
            # authoritative and the next cycle repairs the index rather than the other way round. Replaced
            # rather than appended, because a document that shrank would otherwise leave rows behind that no
            # vector point matches and that the fusion would then rank.
            manifest.remove_chunks(document.relpath)
            index_for_lexical(manifest, points)
            # **The write phase ends here, and the boundary is a choice.** It covers the vector store and
            # both lexical writes, because a document is not ingested until all three have landed. The
            # near-duplicate scan below is a read whose cost belongs to the corpus rather than to this
            # document, so it is deliberately outside the phase.
            timings.write_ms += (time.monotonic() - _write_started) * 1000
            result.timings.add(timings)
            result.timed_documents += 1
            print(timings_line("timing", timings, len(points)))

            # Near-duplicates, asked after the rows are written because the comparison reads them. A pair is
            # recorded and never acted on: two documents a client put in the same folder may be two documents,
            # and a system that deletes one because it looks like the other is worse than one that asks.
            for other, overlap in manifest.near_duplicates(document.relpath,
                                                           DUPLICATE_OVERLAP_THRESHOLD):
                print(f"    near-duplicate: {overlap:.0%} of the smaller document's chunks are shared with "
                      f"{other}. Flagged for review rather than acted on; resolve with --supersede or "
                      f"--distinct.")
                result.duplicates.append((document.relpath, other, overlap))
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
                "payload_version": (record or {}).get("payload_version") or PAYLOAD_VERSION,
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
            "payload_version": PAYLOAD_VERSION,
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
    parser.add_argument("--supersede", metavar="RELPATH",
                        help="declare that a document has been replaced, so an answer may not rely on it. "
                             "Takes --by to name the replacement and --note to say why.")
    parser.add_argument("--by", metavar="RELPATH",
                        help="the document that replaces the one named by --supersede")
    parser.add_argument("--distinct", metavar="RELPATH",
                        help="declare that a flagged near-duplicate is genuinely a separate document, so "
                             "the flag is resolved without superseding anything")
    parser.add_argument("--note", help="why, in your words. A registry entry without one cannot be reviewed.")
    parser.add_argument("--scan-duplicates", action="store_true",
                        help="compare every indexed document against every other and record the pairs worth "
                             "a look. Needed because detection otherwise runs only when a file arrives, so a "
                             "corpus that predates the check is never examined.")
    parser.add_argument("--failures", action="store_true",
                        help="every document that has failed, with the reason and whether it is still "
                             "being retried. Reads the manifest rather than the last cycle, so a "
                             "dead-lettered document is visible")
    parser.add_argument("--versions", action="store_true",
                        help="show the declared currency of every document and any unresolved duplicate flag")
    parser.add_argument("--clear-failures", metavar="RELPATH", nargs="?", const="",
                        help="forget recorded failures so dead-lettered documents are tried again. "
                             "With no argument, all of them. Use after a failure rule changes, because "
                             "the rows written under the old rule cannot know it did.")
    parser.add_argument("--state", default=str(STATE_PATH),
                        help="where the last cycle's result is recorded")
    args = parser.parse_args(argv)

    if args.status:
        return cmd_status(Path(args.state))

    if args.scan_duplicates:
        scanned = build_manifest(os.environ.get("DATABASE_URL"))
        ids = scanned.all_document_ids()
        print(f"comparing {len(ids)} document(s) against each other")
        total = 0
        for doc_id in ids:
            for other, overlap in scanned.near_duplicates(doc_id, DUPLICATE_OVERLAP_THRESHOLD):
                total += 1
        print(f"{total} near-duplicate pair(s) recorded, {len(scanned.unresolved_duplicates())} "
              f"unresolved")
        print()
        print("A pair is recorded, never acted on. Resolve each with --supersede A --by B, or")
        print("--distinct A if they are genuinely two documents. --versions lists what is outstanding.")
        return 0

    if args.failures:
        shown = build_manifest(os.environ.get("DATABASE_URL"))
        stuck = shown.failed_documents()
        if not stuck:
            print("no documents have failed. Every document the worker has seen is indexed.")
            return 0
        print(f"{len(stuck)} document(s) have failed at some point:")
        print()
        for record in stuck:
            count = record.get("failure_count") or 0
            kind = record.get("failure_kind") or "unknown"
            dead = count >= MAX_FAILURES
            state = ("DEAD-LETTERED, not retried until its bytes change" if dead
                     else "will be retried")
            print(f"  {record['relpath']}")
            print(f"    failures: {count} ({kind}), {state}")
            # The reason, which is the whole point of this command. It is stored on every failure and
            # until now nothing read it: `--status` reports the LAST CYCLE's failures, so once a document
            # is dead-lettered and a later cycle succeeds, the document disappears from the one place an
            # operator looks and the deployment reports healthy while a file is silently unindexed.
            error = " ".join(str(record.get("last_error") or "").split())
            if error:
                print(f"    last error: {error[:300]}{'...' if len(error) > 300 else ''}")
            if dead:
                print(f"    try it again with: docker exec ingestion python3 /app/ingest.py "
                      f"--clear-failures {record['relpath']}")
            print()
        # The distinction that matters, stated because getting it wrong wastes an afternoon in one
        # direction and hides a broken document in the other.
        print("Read the error before deciding what to do: **a missing system dependency and a corrupt")
        print("client document both arrive here as `permanent`**, and their fixes have nothing in common.")
        print("A parser refusal naming a tool or a format is ours to fix; a refusal on content is the")
        print("client's file, and the honest answer is to tell them. `docs/manual.md` section 13a lists")
        print("the formats the parser reads and what it needs installed.")
        return 0

    if args.versions:
        shown = build_manifest(os.environ.get("DATABASE_URL"))
        declared = shown.current_documents()
        print(f"{len(declared)} document(s) with a declaration:")
        for doc_id, is_current in sorted(declared.items()):
            print(f"  {'current  ' if is_current else 'SUPERSEDED'} {doc_id}")
        pending = shown.unresolved_duplicates()
        print()
        if pending:
            print(f"{len(pending)} unresolved near-duplicate flag(s):")
            for flag in pending:
                print(f"  {flag['overlap']:.0%} shared  {flag['doc_id']}  <->  {flag['other_doc_id']}")
            print()
            print("Resolve each with --supersede A --by B, or --distinct A if they are separate documents.")
        else:
            print("no unresolved near-duplicate flags")
        return 0

    if args.supersede or args.distinct:
        if args.dry_run:
            print("a declaration needs the real manifest, not a dry run")
            return 1
        declared = build_manifest(os.environ.get("DATABASE_URL"))
        if args.supersede:
            declared.set_current(args.supersede, False, superseded_by=args.by,
                                 note=args.note or f"superseded by {args.by or 'an unstated document'}")
            print(f"{args.supersede} is now superseded"
                  + (f" by {args.by}" if args.by else " by an unstated document")
                  + ". An answer whose only support is this document will be refused rather than answered.")
        if args.distinct:
            # Marking it current is the declaration: it was already current by default, and the point is that
            # somebody looked and resolved the flag.
            declared.set_current(args.distinct, True, note=args.note or "confirmed as a separate document")
            print(f"{args.distinct} is confirmed as a separate document and stays current.")
        return 0

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
        # The contract the vectors are built under, recorded when the collection is established and compared on
        # every write. A mismatch raises rather than warns, because the vectors already in the collection are
        # meaningless under a different contract and similarity against them is not zero, it is nonsense.
        if manifest is not None and getattr(manifest, "connection", None) is not None:
            import contract as contract_module
            live = contract_module.live_contract(services)
            outcome = contract_module.check(manifest.connection, services.collection, live,
                                            allow_change=_contract_change_allowed(),
                                            reason=os.environ.get("EMBED_CONTRACT_REASON"))
            if outcome["status"] != "unchanged":
                print(f"embedding contract {outcome['status']} at generation {outcome['generation']}: "
                      f"{live.get('model_id')}, {live.get('pooling')} pooling, "
                      f"{live.get('dimension')} dimensions")
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
                          "timings_ms": {"parse": round(result.timings.parse_ms),
                                         "chunk": round(result.timings.chunk_ms),
                                         "embed": round(result.timings.embed_ms),
                                         "write": round(result.timings.write_ms)},
                          "timed_documents": result.timed_documents,
                          "failures": result.failures}, indent=2))
    else:
        print()
        print(f"seen {result.seen}, new {result.new}, changed {result.changed}, "
              f"skipped {result.skipped}, failed {result.failed}")
        print(f"chunks written {result.chunks_written}, chunks removed {result.chunks_removed}, "
              f"documents removed {result.documents_removed}")
        # **The line that answers "why is this slow".** Percentages of the time the cycle actually spent
        # ingesting, over the documents it ingested. Absent when nothing was ingested, rather than a row of
        # zeroes that reads like a fast run.
        if result.timed_documents:
            shares = result.timings.shares()
            split = ", ".join(f"{label} {shares.get(field, 0) * 100:.0f}%"
                              for field, label in PHASES)
            print(f"where the time went, over {result.timed_documents} document(s): {split}")
            n = result.timed_documents
            mean = PhaseTimings(parse_ms=result.timings.parse_ms / n,
                                chunk_ms=result.timings.chunk_ms / n,
                                embed_ms=result.timings.embed_ms / n,
                                write_ms=result.timings.write_ms / n)
            mean_chunks = max(1, round(result.chunks_written / max(1, n)))
            print(timings_line("  per document, mean", mean, mean_chunks))
        if result.duplicates:
            print(f"near-duplicate pairs flagged for review: {len(result.duplicates)}")
        for failure in result.failures:
            print(f"  failed: {failure}")

    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
