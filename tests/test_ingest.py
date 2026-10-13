"""The ingestion worker's policy, tested against stub services.

The point of these tests is not that HTTP works. It is that the four decisions which make a re-run safe
are the ones the code actually makes:

  * an unchanged file costs one read and nothing else;
  * a changed file has its old chunks removed rather than joined by new ones;
  * a document whose file has gone is removed from the index, and a file that was never ingested cannot
    cause a removal;
  * a file that fails repeatedly is dead-lettered rather than retried forever.

Every one of those is a decision I got wrong in a first draft, so each has a test that fails if it
reverts. The stubs record what was asked of them, which is what makes "did not re-parse" provable rather
than inferred.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

import chunker  # noqa: E402
import ingest  # noqa: E402


# --------------------------------------------------------------------------- stubs

class StubServices(ingest.Services):
    """Records every call instead of making one, so a test can assert what was NOT called."""

    def __init__(self, parsed: dict | None = None, fail_for: set[str] | None = None,
                 dims: int = 4) -> None:
        super().__init__()
        self.parsed = parsed or simple_document()
        self.fail_for = fail_for or set()
        self.dims = dims
        self.parsed_names: list[str] = []
        self.embedded: list[str] = []
        self.upserted: list[dict] = []
        self.deleted: list[str] = []
        self.embed_batches: int = 0


def simple_document(pages: int = 1, text: str = "A policy note about mileage.") -> dict:
    """A parsed document shaped like the parser's output, small enough to reason about."""
    return {
        "body": {"children": [{"$ref": f"#/texts/{i}"} for i in range(pages)]},
        "texts": [{"self_ref": f"#/texts/{i}", "label": "paragraph", "text": f"{text} Page {i + 1}.",
                   "prov": [{"page_no": i + 1, "bbox": {"l": 53, "t": 700, "b": 660}}]}
                  for i in range(pages)],
        "tables": [],
        "pages": {str(i + 1): {} for i in range(pages)},
    }


@pytest.fixture
def wired(monkeypatch):
    """Point the module's three service calls at stubs and hand back the stub."""
    state = {"services": StubServices()}

    def fake_parse(services, path, relpath):
        services.parsed_names.append(relpath)
        if relpath in services.fail_for:
            raise RuntimeError(f"parser refused {relpath}")
        return services.parsed

    def fake_embed(services, texts):
        services.embed_batches += 1
        services.embedded.extend(texts)
        return [[0.1] * services.dims for _ in texts]

    def fake_upsert(services, points):
        services.upserted.extend(points)

    def fake_delete(services, doc_id):
        services.deleted.append(doc_id)

    # ensure_collection is a separate concern with its own tests; stub it here so a cycle test never
    # reaches for a network.
    monkeypatch.setattr(ingest, "ensure_collection", lambda services: services.dims)
    monkeypatch.setattr(ingest, "parse_document", fake_parse)
    monkeypatch.setattr(ingest, "embed_texts", fake_embed)
    monkeypatch.setattr(ingest, "upsert_points", fake_upsert)
    monkeypatch.setattr(ingest, "delete_document", fake_delete)
    return lambda **kwargs: (state.update(services=StubServices(**kwargs)), state["services"])[1]


def make_corpus(tmp_path: Path, name: str = "company/policy.pdf", content: bytes = b"%PDF-1.4 first"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --------------------------------------------------------------------------- the decisions

def test_an_unchanged_file_costs_one_read_and_no_parse(tmp_path, wired):
    """The reason the manifest holds a hash rather than a timestamp. A file that was touched but not
    edited must not be parsed, embedded, or written again: that is the difference between a cycle that
    costs nothing and one that re-embeds a client's whole corpus nightly."""
    services = wired()
    make_corpus(tmp_path)
    manifest = ingest.MemoryManifest()

    first = ingest.ingest_cycle(services, tmp_path, manifest)
    assert first.new == 1 and first.skipped == 0
    assert services.parsed_names == ["company/policy.pdf"]

    parsed_before = list(services.parsed_names)
    second = ingest.ingest_cycle(services, tmp_path, manifest)
    assert second.skipped == 1 and second.new == 0
    assert services.parsed_names == parsed_before, "an unchanged file was parsed again"


def test_a_changed_file_has_its_old_chunks_removed_not_joined(tmp_path, wired):
    """Delete then write, in that order. Without the delete, a document that shrank leaves its old
    chunks behind and the index holds two versions of the same policy, both citable."""
    services = wired()
    path = make_corpus(tmp_path)
    manifest = ingest.MemoryManifest()

    ingest.ingest_cycle(services, tmp_path, manifest)
    assert services.deleted == [], "nothing should be deleted on a first ingest"

    path.write_bytes(b"%PDF-1.4 second version, longer")
    result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.changed == 1
    assert services.deleted == ["company/policy.pdf"], "the old chunks were not removed"


def test_a_document_whose_file_has_gone_is_removed_from_the_index(tmp_path, wired):
    """Reconciled from the manifest rather than the filesystem, so a file that was never ingested cannot
    cause a removal and a mount that failed to appear cannot empty a client's index."""
    services = wired()
    path = make_corpus(tmp_path)
    manifest = ingest.MemoryManifest()
    ingest.ingest_cycle(services, tmp_path, manifest)

    path.unlink()
    result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.documents_removed == 1
    assert result.chunks_removed >= 1
    assert services.deleted == ["company/policy.pdf"]
    assert manifest.get("company/policy.pdf") is None, "the manifest entry should be gone"


def test_a_file_that_was_never_ingested_cannot_cause_a_removal(tmp_path, wired):
    """The direction of reconciliation, tested directly. An empty documents directory with a populated
    manifest is a disaster; an empty manifest with files present is a first run."""
    services = wired()
    manifest = ingest.MemoryManifest()
    # The manifest knows about a document, and the directory does not have it.
    manifest.put("company/vanished.pdf", {"relpath": "company/vanished.pdf", "root": "company",
                                          "content_sha256": "abc", "chunk_count": 3,
                                          "failure_count": 0})
    result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.documents_removed == 1
    assert services.deleted == ["company/vanished.pdf"]
    # And a fresh corpus with an empty manifest removes nothing.
    services.deleted.clear()
    make_corpus(tmp_path)
    ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())
    assert services.deleted == []


def test_a_failing_file_is_dead_lettered_rather_than_retried_forever(tmp_path, wired):
    """Two failures, then it waits for the bytes to change.

    The first version recorded the previous good hash on failure, and an unreadable file has no previous
    good hash, so it never matched the dead-letter check and was re-parsed every cycle for the life of the
    deployment. Worse, the failure stopped being visible because it was always there.
    """
    services = wired(fail_for={"company/broken.pdf"})
    make_corpus(tmp_path, "company/broken.pdf", b"%PDF-1.4 corrupt")
    manifest = ingest.MemoryManifest()

    first = ingest.ingest_cycle(services, tmp_path, manifest)
    assert first.failed == 1
    assert services.parsed_names.count("company/broken.pdf") == 1

    second = ingest.ingest_cycle(services, tmp_path, manifest)
    assert second.failed == 1
    assert services.parsed_names.count("company/broken.pdf") == 2

    third = ingest.ingest_cycle(services, tmp_path, manifest)
    assert third.failed == 0, "the third cycle should not retry"
    assert third.skipped == 1
    assert services.parsed_names.count("company/broken.pdf") == 2, "it was retried past the limit"
    assert any("dead-lettered" in note for note in third.failures)


def test_a_dead_lettered_file_gets_another_chance_when_its_bytes_change(tmp_path, wired):
    """Dead-lettering is not a blacklist. A client who replaces the corrupt file with a good one should
    not have to tell us."""
    services = wired(fail_for={"company/broken.pdf"})
    path = make_corpus(tmp_path, "company/broken.pdf", b"%PDF-1.4 corrupt")
    manifest = ingest.MemoryManifest()
    ingest.ingest_cycle(services, tmp_path, manifest)
    ingest.ingest_cycle(services, tmp_path, manifest)
    ingest.ingest_cycle(services, tmp_path, manifest)
    assert services.parsed_names.count("company/broken.pdf") == 2

    services.fail_for.clear()
    path.write_bytes(b"%PDF-1.4 now valid")
    result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.failed == 0
    assert result.changed == 1
    assert services.parsed_names.count("company/broken.pdf") == 3


def test_one_bad_document_does_not_stop_the_others(tmp_path, wired):
    """A cycle that aborts on the first corrupt file never ingests anything after it, and the client sees
    a corpus that is missing documents with no error to explain why."""
    services = wired(fail_for={"company/a-broken.pdf"})
    make_corpus(tmp_path, "company/a-broken.pdf", b"%PDF bad")
    make_corpus(tmp_path, "company/b-good.pdf", b"%PDF good")
    make_corpus(tmp_path, "executive/c-good.pdf", b"%PDF good too")
    manifest = ingest.MemoryManifest()

    result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.failed == 1
    assert result.new == 2
    assert manifest.get("company/b-good.pdf") is not None
    assert manifest.get("executive/c-good.pdf") is not None


def test_the_access_label_comes_from_the_top_level_folder(tmp_path, wired):
    """The label is attached at ingest because the search filters on it, and a filter that reads the
    filesystem is not a filter."""
    services = wired()
    make_corpus(tmp_path, "company/handbook/policy.pdf")
    make_corpus(tmp_path, "executive/comp.pdf")
    manifest = ingest.MemoryManifest()
    ingest.ingest_cycle(services, tmp_path, manifest)

    labels = {point["payload"]["doc_id"]: point["payload"]["acl"] for point in services.upserted}
    assert labels["company/handbook/policy.pdf"] == "company"
    assert labels["executive/comp.pdf"] == "executive"


def test_points_carry_what_a_citation_needs(tmp_path, wired):
    """The payload is the citation. Without page numbers and a chunk id, an answer cannot say where it
    came from, and the brief makes that a hard failure rather than a nice-to-have."""
    services = wired()
    make_corpus(tmp_path)
    manifest = ingest.MemoryManifest()
    ingest.ingest_cycle(services, tmp_path, manifest)

    assert services.upserted, "nothing was written"
    for point in services.upserted:
        payload = point["payload"]
        for field in ("chunk_id", "doc_id", "page_start", "page_end", "source_sha256", "acl",
                      "chunker_version", "text"):
            assert payload.get(field) is not None, f"{field} missing from the payload"
        assert payload["page_start"] <= payload["page_end"]
        assert len(point["vector"]) == services.dims


def test_embedding_is_batched_rather_than_one_call_per_chunk(tmp_path, wired):
    """The embedding service is the slowest step, and one request per chunk wastes its batching."""
    services = wired()
    big = simple_document(pages=1, text="A sentence about fees. " * 40)
    services.parsed = big
    make_corpus(tmp_path)
    manifest = ingest.MemoryManifest()
    ingest.ingest_cycle(services, tmp_path, manifest)

    assert services.embed_batches >= 1
    assert len(services.embedded) == len(services.upserted)


def test_a_dry_run_writes_nothing_and_still_reports(tmp_path, wired):
    """A dry run that writes is worse than no dry run, because it is trusted."""
    services = wired()
    make_corpus(tmp_path)
    manifest = ingest.MemoryManifest()
    result = ingest.ingest_cycle(services, tmp_path, manifest, dry_run=True)

    assert result.new == 1
    assert services.upserted == [] and services.deleted == []
    assert manifest.all() == {}


def test_only_accepted_file_types_are_discovered(tmp_path, wired):
    """A client's folder will contain a .DS_Store and a shortcut. Neither is an error, so neither is
    failed; they are simply not documents."""
    make_corpus(tmp_path, "company/policy.pdf")
    make_corpus(tmp_path, "company/notes.docx")
    (tmp_path / "company" / ".DS_Store").write_bytes(b"\x00")
    (tmp_path / "company" / "shortcut.lnk").write_bytes(b"\x00")
    discovered = {document.relpath for document in ingest.discover(tmp_path)}
    assert discovered == {"company/policy.pdf", "company/notes.docx"}


def test_the_same_bytes_produce_the_same_point_ids_across_runs(tmp_path, wired):
    """Stable ids are what let a re-embed update in place instead of duplicating a collection."""
    services = wired()
    make_corpus(tmp_path)
    first = ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())
    ids_first = [point["id"] for point in services.upserted]
    services.upserted.clear()
    services.parsed_names.clear()

    second = ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())
    ids_second = [point["id"] for point in services.upserted]
    assert ids_first == ids_second
    assert first.chunks_written == second.chunks_written


def test_is_ocr_reflects_the_parser_rather_than_defaulting(tmp_path, wired):
    """A figure read by OCR and a figure read from a text layer carry different risk, and the payload is
    where that difference has to survive. The chunker defaults the flag to True because a scanned pipeline
    is the common case; a digital document must not inherit that default."""
    assert ingest._ocr_ran({"texts": [{"prov": [{"origin": "native"}]}]}) is False
    assert ingest._ocr_ran({"texts": [{"prov": [{"origin": "ocr"}]}]}) is True
    assert ingest._ocr_ran({}) is True, "an absent report must assume OCR, the safe direction"

    services = wired()
    services.parsed = simple_document()
    services.parsed["texts"][0]["prov"][0]["origin"] = "native"
    make_corpus(tmp_path)
    ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())
    assert all(point["payload"]["is_ocr"] is False for point in services.upserted)


def test_the_healthcheck_reads_the_last_cycle_rather_than_the_process(tmp_path, capsys):
    """A healthcheck that only proves the process is alive stays green while every document fails. The
    recorded outcome is what makes it mean something, so the three states are tested: nothing yet, a clean
    cycle, and a cycle with a failure."""
    state = tmp_path / "last-cycle.json"
    assert ingest.cmd_status(state) == 1, "no cycle yet should not report healthy"

    state.write_text('{"finished_at": "2026-10-12T19:00:00Z", "counts": {"seen": 4, "failed": 0}, '
                     '"failures": []}')
    assert ingest.cmd_status(state) == 0

    state.write_text('{"finished_at": "2026-10-12T19:05:00Z", "counts": {"seen": 4, "failed": 1}, '
                     '"failures": ["company/broken.pdf: parser refused it"]}')
    assert ingest.cmd_status(state) == 1, "a failed document must make the worker unhealthy"
    assert "company/broken.pdf" in capsys.readouterr().out


def test_a_cycle_records_its_outcome_for_the_healthcheck(tmp_path, wired, monkeypatch):
    """The wiring between the cycle and the healthcheck, which is the part that silently rots: a cycle
    that stops writing its state would leave the healthcheck reporting a stale success forever."""
    services = wired()
    make_corpus(tmp_path)
    state = tmp_path / "state" / "last-cycle.json"
    monkeypatch.setattr(ingest, "STATE_PATH", state)

    manifest = ingest.MemoryManifest()
    result = ingest.ingest_cycle(services, tmp_path, manifest)
    ingest.record_cycle(result)

    assert state.exists()
    assert ingest.cmd_status(state) == 0, "a clean cycle should report healthy"
    import json as _json
    assert _json.loads(state.read_text())["counts"]["seen"] == 1


# --------------------------------------------------------------------------- the collection

def test_the_worker_creates_a_missing_collection_at_the_measured_size(monkeypatch):
    """The gap that made the first real run fail every document with a 404 while reporting healthy.

    The collection is a dependency of every write, and it was created by a manual step in the runbook that
    a fresh vector store does not have. The worker now establishes it, at the size the embedding service
    actually produces rather than a constant in the file."""
    calls = {"put": [], "get": []}

    monkeypatch.setattr(ingest, "_get", lambda url, timeout: calls["get"].append(url) or None)
    monkeypatch.setattr(ingest, "_post",
                        lambda url, body, timeout, method="POST":
                        calls["put"].append((url, body, method)) or {})
    monkeypatch.setattr(ingest, "_get_vector_size", lambda services: 1024)

    size = ingest.ensure_collection(ingest.Services())
    assert size == 1024
    assert len(calls["put"]) == 1
    url, body, method = calls["put"][0]
    assert url.endswith("/collections/documents")
    assert method == "PUT"
    assert body == {"vectors": {"size": 1024, "distance": "Cosine"}}


def test_a_collection_of_the_wrong_size_is_refused_not_reconciled(monkeypatch):
    """Recreating it would delete a client's index; using it would reject every write. Both are decisions
    rather than repairs, so the error names the mismatch and stops."""
    monkeypatch.setattr(ingest, "_get", lambda url, timeout: {
        "config": {"params": {"vectors": {"size": 768, "distance": "Cosine"}}}, "points_count": 0})
    monkeypatch.setattr(ingest, "_get_vector_size", lambda services: 1024)
    monkeypatch.setattr(ingest, "_post",
                        lambda *a, **k: pytest.fail("a mismatched collection must not be modified"))

    with pytest.raises(RuntimeError) as raised:
        ingest.ensure_collection(ingest.Services())
    message = str(raised.value)
    assert "768" in message and "1024" in message
    assert "deletes every chunk" in message, "the error should say why it will not fix it"


def test_a_collection_using_the_wrong_distance_is_refused(monkeypatch):
    """Cosine is what every measurement so far was taken under. A collection built with another metric
    would return results in a different order and nothing would say so."""
    monkeypatch.setattr(ingest, "_get", lambda url, timeout: {
        "config": {"params": {"vectors": {"size": 1024, "distance": "Euclid"}}}, "points_count": 5})
    monkeypatch.setattr(ingest, "_get_vector_size", lambda services: 1024)
    monkeypatch.setattr(ingest, "_post", lambda *a, **k: pytest.fail("must not modify"))
    with pytest.raises(RuntimeError) as raised:
        ingest.ensure_collection(ingest.Services())
    assert "Euclid" in str(raised.value)


def test_an_existing_correct_collection_is_left_alone(monkeypatch):
    """The idempotent case, which is what every cycle after the first hits."""
    monkeypatch.setattr(ingest, "_get", lambda url, timeout: {
        "config": {"params": {"vectors": {"size": 1024, "distance": "Cosine"}}}, "points_count": 1183})
    monkeypatch.setattr(ingest, "_get_vector_size", lambda services: 1024)
    monkeypatch.setattr(ingest, "_post", lambda *a, **k: pytest.fail("nothing should be created"))
    assert ingest.ensure_collection(ingest.Services()) == 1024


def test_the_dimension_comes_from_the_service_not_a_constant(monkeypatch):
    """The probe found /embed first and /v1/embeddings also working, and /embeddings refused with 422.
    The measurement tries the shapes that answer and reports rather than guessing a size."""
    def fake_post(url, body, timeout, method="POST"):
        if url.endswith("/embed"):
            return [[0.0] * 1024]
        raise AssertionError("should not reach the second shape when the first answers")
    monkeypatch.setattr(ingest, "_post", fake_post)
    assert ingest._get_vector_size(ingest.Services()) == 1024

    def openai_shape(url, body, timeout, method="POST"):
        if url.endswith("/embed"):
            raise RuntimeError("this deployment refuses /embed")
        return {"data": [{"embedding": [0.0] * 768}]}
    monkeypatch.setattr(ingest, "_post", openai_shape)
    assert ingest._get_vector_size(ingest.Services()) == 768

    def nothing_works(url, body, timeout, method="POST"):
        raise RuntimeError("refused")
    monkeypatch.setattr(ingest, "_post", nothing_works)
    with pytest.raises(RuntimeError) as raised:
        ingest._get_vector_size(ingest.Services())
    assert "guess" in str(raised.value).lower()


def test_an_unreferenced_body_is_recovered_rather_than_lost(tmp_path, wired):
    """The shape that produced 0 chunks on the first real corpus run, at the chunker's layer.

    The body's children are the traversal, so a text item not referenced from there was invisible however
    much content it held. Two HTML contract exhibits went into the index as empty documents that looked
    retrievable. The chunker now falls back to every text item when the walk finds nothing, so the first
    line of defence is recovery rather than refusal.
    """
    services = wired()
    # Body references no items, and the text is present. This is what the exhibits did.
    services.parsed = {"body": {"children": []},
                       "texts": [{"self_ref": "#/texts/0", "label": "paragraph",
                                  "text": "A clause that must be retrievable.",
                                  "prov": [{"page_no": 1, "bbox": {"l": 53, "t": 700, "b": 660}}]}],
                       "tables": [], "pages": {"1": {}}}
    make_corpus(tmp_path, "company/contract.htm", b"<html>a contract</html>")
    result = ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())

    assert result.new == 1, "the text was there and should have been recovered"
    assert len(services.upserted) == 1
    assert "retrievable" in services.upserted[0]["payload"]["text"]


def test_a_document_with_no_text_at_all_is_refused_rather_than_recorded(tmp_path, wired):
    """The second line of defence, and the case recovery cannot help with.

    A scanned page the OCR could not read, or an HTML page whose content is entirely an image. A failed
    document is visible and retried; an empty one sits in the index looking retrievable and answers
    nothing, with no error anywhere to explain why."""
    services = wired()
    services.parsed = {"body": {"children": []}, "texts": [], "tables": [], "pages": {"1": {}}}
    make_corpus(tmp_path, "company/blank.htm", b"<html></html>")
    result = ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())
    assert result.failed == 1
    assert services.upserted == []


# --------------------------------------------------------------------------- retries

def test_a_gateway_timeout_is_retried(monkeypatch):
    """The first real corpus run lost a 60-page PDF to a 504 while parsing, and the stage behind it
    genuinely takes minutes on CPU. The parse is idempotent, so trying again costs time and nothing else.
    A run that gives up on the first 5xx fails on the documents that take longest to read."""
    import urllib.error

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.HTTPError("http://parser", 504, "Gateway Timeout", {}, None)
        return {"ok": True}

    monkeypatch.setattr(ingest, "RETRY_BACKOFF_SECONDS", 0)
    assert ingest._with_retries("parsing x", flaky) == {"ok": True}
    assert attempts["n"] == 3


def test_a_client_error_is_not_retried(monkeypatch):
    """A 4xx will fail the same way, and retrying it turns a clear error into a slow one. The parser's 422
    for a malformed request is the case that matters: retrying it three times hides the real problem."""
    import urllib.error

    attempts = {"n": 0}

    def refused():
        attempts["n"] += 1
        raise urllib.error.HTTPError("http://parser", 422, "Unprocessable Entity", {}, None)

    monkeypatch.setattr(ingest, "RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(urllib.error.HTTPError):
        ingest._with_retries("parsing x", refused)
    assert attempts["n"] == 1, "a 422 must not be retried"


def test_retries_give_up_rather_than_looping_forever(monkeypatch):
    """Three attempts and then the error, so a permanently broken service surfaces as a failed document
    rather than a cycle that never finishes."""
    import urllib.error

    attempts = {"n": 0}

    def always_down():
        attempts["n"] += 1
        raise urllib.error.HTTPError("http://parser", 503, "Unavailable", {}, None)

    monkeypatch.setattr(ingest, "RETRY_BACKOFF_SECONDS", 0)
    with pytest.raises(urllib.error.HTTPError):
        ingest._with_retries("parsing x", always_down)
    assert attempts["n"] == ingest.RETRY_ATTEMPTS


# --------------------------------------------------------------------------- transient failures

def test_a_transient_failure_does_not_dead_letter_the_document(tmp_path, wired):
    """The second real-corpus bug. A 60-page PDF was dead-lettered after two 504s from the parser, and the
    document was fine: the stage behind it is slow and something in front of it gave up.

    Counting that against the file leaves a permanently unreadable corpus that no retry fixes and no log
    explains, because the entry says the document failed rather than that the service did."""
    import urllib.error

    services = wired()
    make_corpus(tmp_path, "company/slow.pdf", b"%PDF a long one")
    manifest = ingest.MemoryManifest()

    # A gateway timeout, twice, which is exactly what happened.
    def timed_out(services_, path, relpath):
        raise urllib.error.HTTPError("http://parser", 504, "Gateway Timeout", {}, None)
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(ingest, "parse_document", timed_out)
        first = ingest.ingest_cycle(services, tmp_path, manifest)
        second = ingest.ingest_cycle(services, tmp_path, manifest)
    assert first.failed == 1 and second.failed == 1
    record = manifest.get("company/slow.pdf")
    assert record["failure_kind"] == "transient"
    assert record["failure_count"] == 0, "a transient failure must not advance the count towards dead-letter"

    # And on the third cycle the service is well again, so the document ingests. It reports `changed`
    # rather than `new` because the manifest already knows the path from the failed attempts, which is the
    # right way round: the document was seen, it failed, and now it succeeded.
    third = ingest.ingest_cycle(services, tmp_path, manifest)
    assert third.failed == 0, "a transient failure must leave the document retryable"
    assert third.changed == 1
    assert third.chunks_written >= 1
    assert manifest.get("company/slow.pdf")["failure_count"] == 0
    assert manifest.get("company/slow.pdf")["failure_kind"] is None


def test_a_permanent_failure_still_dead_letters(tmp_path, wired):
    """The limit has to keep working for the failures it was written for: a document that cannot be parsed
    for its own reasons should stop being retried every cycle."""
    services = wired(fail_for={"company/broken.pdf"})
    make_corpus(tmp_path, "company/broken.pdf", b"not really a pdf")
    manifest = ingest.MemoryManifest()

    ingest.ingest_cycle(services, tmp_path, manifest)
    ingest.ingest_cycle(services, tmp_path, manifest)
    record = manifest.get("company/broken.pdf")
    assert record["failure_kind"] == "permanent"
    assert record["failure_count"] == 2

    third = ingest.ingest_cycle(services, tmp_path, manifest)
    assert third.failed == 0 and third.skipped == 1
    assert any("dead-lettered" in note for note in third.failures)


def test_the_kind_of_failure_is_decided_from_the_exception():
    """The classification itself, which everything above depends on."""
    import urllib.error

    for code in (429, 500, 502, 503, 504):
        assert ingest.is_transient(
            urllib.error.HTTPError("u", code, "m", {}, None)) is True, code
    for code in (400, 404, 422):
        assert ingest.is_transient(
            urllib.error.HTTPError("u", code, "m", {}, None)) is False, code
    assert ingest.is_transient(TimeoutError("took too long")) is True
    assert ingest.is_transient(ConnectionError("refused")) is True
    assert ingest.is_transient(RuntimeError("no retrievable text")) is False


# --------------------------------------------------------------------------- repairing old records

def test_a_record_claiming_success_with_no_chunks_is_re_ingested(tmp_path, wired):
    """The residue of a bug the fix cannot reach by itself.

    The first corpus run wrote two contract exhibits as zero chunks and recorded them as ingested. Those
    rows say "unchanged, 0 chunks" ever since, so the hash check skips them forever and no fix to the
    chunker ever reaches those documents. A manifest is a cache of a claim about the index, and a record
    with nothing behind it is a false claim whatever wrote it.
    """
    services = wired()
    make_corpus(tmp_path, "company/contract.htm", b"<html>a contract</html>")
    manifest = ingest.MemoryManifest()
    # The false claim, exactly as the old code wrote it.
    manifest.put("company/contract.htm", {"relpath": "company/contract.htm", "root": "company",
                                          "content_sha256": ingest.sha256_of(
                                              tmp_path / "company" / "contract.htm")[0],
                                          "chunk_count": 0, "failure_count": 0})

    result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.skipped == 0, "a record with no chunks behind it must not be trusted"
    assert result.changed == 1
    assert result.chunks_written >= 1
    assert services.parsed_names == ["company/contract.htm"]


def test_clearing_failures_makes_a_dead_lettered_document_retryable(tmp_path, wired):
    """What an operator needs when a failure rule changes.

    The 60-page PDF was dead-lettered over two gateway timeouts. When failures learned to tell an outage
    from a corrupt file, that row still said "two failures" and the document stayed dead, because a row
    cannot know the rule that wrote it has been replaced. The alternative to a repair path is telling an
    operator to delete rows by hand from a table they have no reason to know about.
    """
    services = wired(fail_for={"company/broken.pdf"})
    make_corpus(tmp_path, "company/broken.pdf", b"not a pdf")
    manifest = ingest.MemoryManifest()
    ingest.ingest_cycle(services, tmp_path, manifest)
    ingest.ingest_cycle(services, tmp_path, manifest)
    assert manifest.get("company/broken.pdf")["failure_count"] == 2

    # Still dead-lettered.
    assert ingest.ingest_cycle(services, tmp_path, manifest).skipped == 1

    assert manifest.clear_failures() == 1
    record = manifest.get("company/broken.pdf")
    assert record["failure_count"] == 0
    assert record["failure_kind"] is None

    # And now it is attempted again.
    services.fail_for.clear()
    result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.failed == 0
    assert result.chunks_written >= 1


def test_clearing_one_document_leaves_the_others_alone(tmp_path, wired):
    """Scoped repair, so fixing one file does not silently retry a corpus of genuinely broken ones."""
    manifest = ingest.MemoryManifest()
    for name in ("a.pdf", "b.pdf"):
        manifest.put(name, {"relpath": name, "root": "company", "content_sha256": "x",
                            "chunk_count": 0, "failure_count": 2, "failure_kind": "permanent"})
    assert manifest.clear_failures("a.pdf") == 1
    assert manifest.get("a.pdf")["failure_count"] == 0
    assert manifest.get("b.pdf")["failure_count"] == 2
