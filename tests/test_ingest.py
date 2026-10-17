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
import urllib.error
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
        # A document with a digital text layer, which is what a simple fixture represents.
        self.confidence: dict = {"parse_score": 1.0, "ocr_score": 0.9}
        # No page carried an OCR score, so nothing in this fixture was read off an image.
        self.confidence_pages: dict = {"1": {"parse_score": 1.0, "ocr_score": None}}
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
        # The real call returns the document with its confidence report beside it, because is_ocr is
        # decided from that report. The stub mirrors the shape rather than the document alone.
        return {"document": services.parsed, "confidence": services.confidence,
                "confidence_pages": services.confidence_pages}

    def fake_embed(services, texts):
        services.embed_batches += 1
        services.embedded.extend(texts)
        return [[0.1] * services.dims for _ in texts]

    def fake_count_tokens(services, texts):
        # The measured post-condition uses the serving model's tokenizer; a deterministic stand-in keeps the
        # cycle tests off the network while still exercising the enforcement (RET-CHUNK-001).
        return [max(1, (len(text) + 3) // 4) for text in texts]

    def fake_upsert(services, points):
        services.upserted.extend(points)

    def fake_delete(services, doc_id):
        services.deleted.append(doc_id)

    # ensure_collection is a separate concern with its own tests; stub it here so a cycle test never
    # reaches for a network.
    monkeypatch.setattr(ingest, "ensure_collection", lambda services: services.dims)
    monkeypatch.setattr(ingest, "parse_document", fake_parse)
    monkeypatch.setattr(ingest, "embed_texts", fake_embed)
    monkeypatch.setattr(ingest, "count_tokens", fake_count_tokens)
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
    failed; they are simply not documents, and neither belongs in the unreadable count either, because a
    number that includes debris is a number an operator learns to ignore."""
    make_corpus(tmp_path, "company/policy.pdf")
    make_corpus(tmp_path, "company/notes.docx")
    (tmp_path / "company" / ".DS_Store").write_bytes(b"\x00")
    (tmp_path / "company" / "shortcut.lnk").write_bytes(b"\x00")
    discovered, unreadable = ingest.discover(tmp_path)
    assert {document.relpath for document in discovered} == {"company/policy.pdf", "company/notes.docx"}
    assert unreadable == [], "operating-system debris is not an unreadable document"


def test_a_format_the_worker_does_not_read_is_reported_rather_than_skipped(tmp_path, wired):
    """**The failure a client cannot see, and the reason this test exists.**

    An unsupported format used to be dropped inside `discover` with no count, no log line and no error: the
    file was in the client's folder, absent from every search, and nothing anywhere said so. For a product
    that promises a client's documents are searchable, that is the worst available outcome, because the only
    way to discover it is to look for something and not find it.

    So the name is returned and the cycle prints it. The cases here are formats a small business genuinely
    has and this pipeline genuinely does not read, which is a different list from the one it used to be: the
    scan and the legacy spreadsheet beside them are read now, and `test_the_formats_the_parser_advertises_are
    _read_or_excluded` is what keeps that honest.
    """
    make_corpus(tmp_path, "company/policy.pdf")
    for name in ("company/message.msg", "company/network-diagram.vsd", "company/accounts.accdb"):
        (tmp_path / name).write_bytes(b"\x00" * 16)
    discovered, unreadable = ingest.discover(tmp_path)
    assert {d.relpath for d in discovered} == {"company/policy.pdf"}
    assert sorted(unreadable) == ["company/accounts.accdb", "company/message.msg",
                                  "company/network-diagram.vsd"]


def test_the_formats_a_client_keeps_records_in_are_read(tmp_path, wired):
    """**The regression guard for the gap this change closes.**

    A scanner produces a TIFF; a phone produces a JPEG; a bookkeeper has kept an `.xls` since 2009. Every one
    of those was refused in silence, and the parser could read all of them: OCR is installed and works, and
    LibreOffice reads the legacy spreadsheet once its Calc component is present. The allowlist was the only
    thing in the way.
    """
    wanted = ["company/scan.tiff", "company/scan.tif", "company/photo.jpg", "company/photo.jpeg",
              "company/screenshot.png", "company/ledger.xls", "company/deck.ppt", "company/letter.doc",
              "company/macro-sheet.xlsm", "company/template.dotx", "company/saved-page.mht",
              "company/report.potx"]
    make_corpus(tmp_path, "company/policy.pdf")
    for name in wanted:
        (tmp_path / name).write_bytes(b"\x00" * 16)
    discovered, unreadable = ingest.discover(tmp_path)
    assert unreadable == [], f"these should all be read now: {unreadable}"
    assert {d.relpath for d in discovered} == set(wanted) | {"company/policy.pdf"}


def test_the_cycle_counts_the_files_it_could_not_read(tmp_path, wired):
    """The count reaches the result, because a number that stops at `discover` reaches nobody."""
    services = wired()
    make_corpus(tmp_path, "company/policy.pdf")
    (tmp_path / "company" / "message.msg").write_bytes(b"\x00" * 16)
    result = ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())
    assert result.unreadable == 1
    assert result.unreadable_files == ["company/message.msg"]
    assert result.as_counts()["unreadable"] == 1, "the count has to survive into the machine-readable output"


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


def test_is_ocr_comes_from_the_confidence_report(tmp_path, wired):
    """A figure read by OCR and a figure read from a text layer carry different risk, and the payload is
    where that difference has to survive.

    The first version looked for an `origin` field on each item's provenance. This parser does not emit
    one: provenance carries page_no, bbox and charspan and nothing else. The search never matched, the
    fallback answered True every time, and an HTML contract was labelled as machine-read text. These are
    the measured values from the box, so the test fails if the signal changes.
    """
    # A scan: no digital text cells, so parse_score is null.
    assert ingest._ocr_ran({}, {"parse_score": None, "ocr_score": 0.989}) is True
    # A text PDF: a text layer exists, so parse_score is a number.
    assert ingest._ocr_ran({}, {"parse_score": 1.0, "ocr_score": 0.828}) is False
    # No report at all: assume OCR, which labels a figure as machine-read rather than presenting a
    # machine's guess as text that was already there.
    assert ingest._ocr_ran({}, None) is True
    assert ingest._ocr_ran({}, {}) is True

    services = wired()
    services.confidence = {"parse_score": 1.0}
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


# --------------------------------------------------------------------------- the parser contract

def test_the_conversion_options_are_ones_the_service_accepts():
    """Three of the four options in the first version were ignored by the parser, which answered success
    anyway. `do_ocr`, `do_table_structure` and `table_mode` were not options of docling-serve 1.36, and it
    accepted them by ignoring them, which is the failure this whole line of work came out of.

    The service this talks to now is `services/parser`, which refuses an unknown option and names the ones
    it accepts, so a wrong name fails loudly at the first document instead of silently for a year. This test
    is still a list rather than a live check, because CI has no parser; the live check is in the runbook.
    """
    assert set(ingest.PARSE_OPTIONS) == {
        "to_formats", "image_export_mode", "ocr", "table_structure"}, (
        "the option set is asserted because an ignored option looks exactly like an accepted one")
    for not_an_option in ("do_ocr", "do_table_structure", "table_mode", "ocr_preset",
                          "table_structure_preset", "force_ocr"):
        assert not_an_option not in ingest.PARSE_OPTIONS, (
            f"{not_an_option} is not an option of services/parser; it would be refused, which is correct "
            f"but not what we want to send")
    # And OCR must be left to decide per page rather than forced, because forcing it rasterises pages that
    # already have a text layer.
    assert ingest.PARSE_OPTIONS["ocr"] == "true"


def test_parsing_uses_the_async_endpoint_rather_than_the_capped_one(tmp_path):
    """The synchronous endpoint gives up after DOCLING_SERVE_MAX_SYNC_WAIT, 120 seconds by default, and
    does not abort the task when it does. On a 59-page PDF that meant every attempt returned 504 while a
    parse kept running, so each retry orphaned work and started more. One docling process accumulated over
    six hours of CPU that way.

    Asserted against the URL the code builds, because the difference between the two endpoints is one path
    segment and the failure it causes is a timeout rather than an error.
    """
    seen = {}

    def capture(url, filename, payload, options, timeout):
        seen["url"] = url
        return {"task_id": "t-1", "task_status": "pending"}

    class OnePoll:
        def __init__(self):
            self.calls = 0

    polls = OnePoll()

    def fake_get_json(url, timeout):
        if "/status/poll/" in url:
            polls.calls += 1
            return {"task_status": "success"}
        return {"document": {"json_content": {"texts": [{"text": "x"}], "pages": {"1": {}}}}}

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(ingest, "_multipart", capture)
        patch.setattr(ingest, "_get_json", fake_get_json)
        patch.setattr(ingest, "time", ingest.time)
        source = tmp_path / "x.pdf"
        source.write_bytes(b"%PDF-1.4 test")
        document = ingest.parse_document(ingest.Services(), source, "company/x.pdf")

    # The async path contains the sync path as a prefix, so the check has to be the suffix rather than
    # the absence of the shorter one.
    assert seen["url"].endswith("/v1/convert/file/async"), seen["url"]
    assert document["document"]["texts"], "the parsed document should come back"
    assert "confidence" in document, "the confidence report travels with the document"
    assert polls.calls == 1, "the task should be waited for rather than assumed"


def test_a_parse_that_exceeds_its_budget_raises_rather_than_resubmitting(tmp_path):
    """The task is still running on the box when this happens, and the message says so. Submitting again
    is what turned one slow document into several orphaned parses."""
    import pytest as _pytest

    class Expired(ingest.Services):
        """The real service record with a budget of zero, so the wait expires immediately."""
        def __init__(self):
            super().__init__()
            self.parse_timeout = 0.0

    source = tmp_path / "x.pdf"
    source.write_bytes(b"%PDF-1.4 test")

    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(ingest, "_multipart",
                      lambda url, filename, payload, options, timeout:
                      {"task_id": "still-running", "task_status": "pending"})
        patch.setattr(ingest, "_get_json", lambda url, timeout: {"task_status": "pending"})
        with _pytest.raises(TimeoutError) as raised:
            ingest.parse_document(Expired(), source, "company/x.pdf")
    message = str(raised.value)
    assert "still running on the box" in message
    assert "still-running" in message, "the message must name the task so it can be found"


# --------------------------------------------------------------------------- per-page OCR

def test_ocr_pages_reads_the_scanned_pages_from_the_report():
    """The fact the pipeline could not have before this work.

    Measured on the fixture corpus's 59-page publication: pages 1 and 8 carry an `ocr_score` and the other
    57 have null. A page with a score was read off an image; a page without one was not touched by OCR. That
    is the whole signal, and it is the reason services/parser exists.
    """
    report = {
        "1": {"ocr_score": 0.7284, "parse_score": None},
        "2": {"ocr_score": None, "parse_score": 1.0},
        "3": {"ocr_score": None, "parse_score": 1.0},
        "8": {"ocr_score": 0.9282, "parse_score": None},
        "59": {"ocr_score": None, "parse_score": 1.0},
    }
    assert ingest.ocr_pages(report) == {1, 8}
    assert ingest.ocr_pages({}) == set()
    assert ingest.ocr_pages(None) == set()
    # A malformed key is skipped rather than raising, because a page number that will not parse should cost
    # one page's label and not the document.
    assert ingest.ocr_pages({"x": {"ocr_score": 0.5}, "4": {"ocr_score": 0.5}}) == {4}


def test_is_ocr_is_set_per_chunk_from_the_page_it_came_from(tmp_path, wired):
    """A mixed filing labelled one way for the whole document is the version that misleads a reader.

    The fixture's construction: pages 1 and 3 digital, pages 2 and 4 scanned. A chunk on page 2 was read off
    an image and must say so; a chunk on page 1 must not. Before services/parser both were labelled the same
    way, and the direction of the error was the dangerous one, because text recognised from a scan was
    presented as text that was already in the file.
    """
    services = wired()
    services.confidence = {"parse_score": 1.0}
    services.confidence_pages = {
        "1": {"ocr_score": None, "parse_score": 1.0},
        "2": {"ocr_score": 0.99, "parse_score": None},
        "3": {"ocr_score": None, "parse_score": 1.0},
        "4": {"ocr_score": 0.98, "parse_score": None},
    }
    # One text item per page, so each becomes its own chunk.
    services.parsed = {
        "pages": {str(p): {} for p in (1, 2, 3, 4)},
        "body": {"children": [{"$ref": f"#/texts/{i}"} for i in range(4)]},
        "texts": [{"self_ref": f"#/texts/{i}", "label": "paragraph",
                   "text": f"Rates for page {i + 1}: RATE-{i + 1}00.",
                   "prov": [{"page_no": i + 1, "bbox": {"l": 53, "t": 700, "b": 660}}]}
                  for i in range(4)],
        "tables": [],
    }
    make_corpus(tmp_path, "company/mixed.pdf", b"%PDF mixed")
    ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())

    by_page = {point["payload"]["page_start"]: point["payload"] for point in services.upserted}
    assert set(by_page) == {1, 2, 3, 4}
    assert by_page[1]["is_ocr"] is False, "page 1 is digital"
    assert by_page[2]["is_ocr"] is True, "page 2 is a scan"
    assert by_page[3]["is_ocr"] is False, "page 3 is digital"
    assert by_page[4]["is_ocr"] is True, "page 4 is a scan"
    # And the count travels, so a reader can be told a figure is partly machine-read.
    assert by_page[2]["ocr_page_count"] == 1
    assert by_page[1]["ocr_page_count"] == 0


def test_a_document_with_no_per_page_report_falls_back_to_the_rollup(tmp_path, wired):
    """docling-serve never sends confidence_pages, and a format the parser does not score sends none
    either. The field then falls back to the document-level answer, which is exactly what the worker did
    before services/parser existed. A fallback that silently answered "not OCR" would be the dangerous
    direction."""
    services = wired()
    services.confidence = {"parse_score": None, "ocr_score": 0.5}
    services.confidence_pages = {}
    make_corpus(tmp_path)
    ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())
    assert all(point["payload"]["is_ocr"] is True for point in services.upserted)
    assert all(point["payload"]["ocr_page_count"] == 0 for point in services.upserted)


def test_a_format_the_scorer_did_not_look_at_is_not_labelled_ocr():
    """The corpus's two HTML contract exhibits, which came back from the service labelled as machine-read.

    The parser runs its scoring stages over PDFs and returns a full set of nulls with both grades set to
    "unspecified" for a format it does not score. That is not an empty report, so testing only for
    emptiness fell through to the parse_score branch, where a null parse_score means "no digital text
    cells, therefore OCR" — true for a scan, wrong for HTML, which has no pages to have read either way.

    The direction matters: text that nothing performed recognition on was being presented as machine-read,
    so a reader would distrust a contract's plain text for no reason.
    """
    unspecified = {"parse_score": None, "layout_score": None, "table_score": None, "ocr_score": None,
                   "mean_score": None, "low_score": None,
                   "mean_grade": "unspecified", "low_grade": "unspecified"}
    assert ingest._ocr_ran({}, unspecified) is False

    # And a scan still reads as OCR, because its scorer ran and found no digital text cells.
    scanned = {"parse_score": None, "layout_score": 0.889, "table_score": None, "ocr_score": 0.989,
               "mean_score": 0.939, "low_score": 0.894,
               "mean_grade": "excellent", "low_grade": "good"}
    assert ingest._ocr_ran({}, scanned) is True

    # A text PDF still reads as not-OCR.
    text_pdf = {"parse_score": 1.0, "layout_score": 0.930, "table_score": None, "ocr_score": 0.828,
                "mean_score": 0.964, "low_score": 0.933,
                "mean_grade": "excellent", "low_grade": "excellent"}
    assert ingest._ocr_ran({}, text_pdf) is False

    # An all-null report with no grade at all is the same situation.
    assert ingest._ocr_ran({}, {name: None for name in
                                ("parse_score", "layout_score", "ocr_score")}) is False
    # And no report at all still assumes the safe direction.
    assert ingest._ocr_ran({}, None) is True


# --------------------------------------------------------------------------- propagating a code change

def test_a_payload_rule_change_re_derives_a_document_whose_bytes_did_not_change(tmp_path, wired):
    """The gap that made a fix invisible.

    The skip decision is made on the file's bytes, which is right for a file that was edited and wrong for a
    change to the code that derives the payload from those bytes. Then the bytes match, the manifest still
    believes its old claim, and the new logic never reaches the documents it was written for. It happened
    twice in the ingestion step: once for how empty documents are refused, and once for is_ocr, where all 29
    chunks of two HTML exhibits stayed labelled machine-read after the code that labelled them was fixed.

    An earlier fix to the same staleness class caught only the zero-chunk case, which is a symptom. This is
    the mechanism.
    """
    services = wired()
    make_corpus(tmp_path, "company/policy.pdf", b"%PDF unchanged bytes")
    manifest = ingest.MemoryManifest()

    first = ingest.ingest_cycle(services, tmp_path, manifest)
    assert first.new == 1
    record = manifest.get("company/policy.pdf")
    assert record["payload_version"] == ingest.PAYLOAD_VERSION

    # An unchanged file under the current rules is still skipped.
    assert ingest.ingest_cycle(services, tmp_path, manifest).skipped == 1

    # Now the rules change, the way they did when is_ocr was corrected and again when the lexical store was
    # added. The fake version is derived from the real one rather than written out, because a literal stops
    # being a change the moment the real value catches up with it: this test passed for the wrong reason for
    # exactly as long as it took PAYLOAD_VERSION to reach the string it named.
    changed_rules = ingest.PAYLOAD_VERSION + "-test"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ingest, "PAYLOAD_VERSION", changed_rules)
        services.parsed_names.clear()
        result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.skipped == 0, "an unchanged file under older rules must be re-derived"
    assert result.changed == 1
    assert services.parsed_names == ["company/policy.pdf"], "it must be parsed again to re-derive the payload"
    assert manifest.get("company/policy.pdf")["payload_version"] == changed_rules


def test_a_record_from_before_the_version_existed_is_also_re_derived(tmp_path, wired):
    """Rows written before this column existed have no version, and a comparison against a missing value
    must read as stale rather than as current. The alternative is a corpus that keeps its pre-fix labels
    forever on the documents nobody happens to edit."""
    services = wired()
    make_corpus(tmp_path, "company/policy.pdf", b"%PDF old record")
    manifest = ingest.MemoryManifest()
    manifest.put("company/policy.pdf", {
        "relpath": "company/policy.pdf", "root": "company",
        "content_sha256": ingest.sha256_of(tmp_path / "company" / "policy.pdf")[0],
        "chunk_count": 1, "failure_count": 0,
        # No payload_version key at all, which is what migration 009 left behind.
    })
    result = ingest.ingest_cycle(services, tmp_path, manifest)
    assert result.skipped == 0
    assert result.changed == 1


# --------------------------------------------------------------------------- concurrency

def many(tmp_path: Path, count: int) -> None:
    """A corpus of `count` distinct documents, all of which the stub parser will accept."""
    for i in range(count):
        make_corpus(tmp_path, name=f"company/doc{i:02d}.pdf", content=b"%PDF-1.4 doc " + str(i).encode())


def test_several_documents_are_prepared_at_once(monkeypatch, tmp_path, wired):
    """**The change this exists to prove: the waits overlap.**

    Measured on the deployment, parsing is 44 to 47 percent of an ingest's wall clock and embedding 50 to 53,
    and the parser container uses about one core of sixteen. The worker ran one document at a time, so while
    it waited on the parser the embedder was idle, and the other way round. Neither service was saturated,
    and fifteen cores were idle throughout.

    This asserts the overlap rather than timing it: with the window at four and a parse that sleeps, four
    documents must be inside the parser at the same moment.
    """
    import threading
    import time as _time

    services = wired()
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}
    inner = ingest.parse_document

    def slow_parse(services_, path, relpath):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        try:
            _time.sleep(0.15)
            return inner(services_, path, relpath)
        finally:
            with lock:
                live["now"] -= 1

    monkeypatch.setattr(ingest, "parse_document", slow_parse)
    # No override of INGEST_CONCURRENCY: the test reads the worker's real window, because a test that sets
    # the value it is checking passes against a serial worker and measures nothing.
    assert ingest.INGEST_CONCURRENCY > 1, "the window is one, so this test would prove nothing"

    many(tmp_path, 8)
    result = ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())

    assert result.new == 8, f"expected eight documents ingested, got {result.new}"
    assert live["peak"] > 1, (
        "documents were parsed one at a time, so nothing overlapped and the cycle is still serial")


def test_the_window_bounds_how_many_are_in_flight(monkeypatch, tmp_path, wired):
    """**A window rather than a stampede.** The first version of this change submitted every document at
    once, which held all ten thousand documents' vectors in memory and wrote nothing for hours. A batch of
    sixteen embedding requests also answered `429 Model is overloaded`, so the bound is measured rather than
    cautious.
    """
    import threading
    import time as _time

    services = wired()
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}
    inner = ingest.parse_document

    def counting_parse(services_, path, relpath):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        try:
            _time.sleep(0.05)
            return inner(services_, path, relpath)
        finally:
            with lock:
                live["now"] -= 1

    monkeypatch.setattr(ingest, "parse_document", counting_parse)
    window = ingest.INGEST_CONCURRENCY

    many(tmp_path, 12)
    ingest.ingest_cycle(services, tmp_path, ingest.MemoryManifest())

    assert live["peak"] <= window, (
        f"{live['peak']} documents were in flight against a window of {window}, so the bound is not holding")
    assert live["peak"] > 1, "nothing overlapped, so the cycle is still serial"


def test_a_failure_under_concurrency_is_attributed_to_its_own_document(monkeypatch, tmp_path, wired):
    """**The property concurrency could easily have destroyed.** A batch-level retry would mark every
    document in the batch failed when one of them was. Each document's work is its own future, so a failure
    belongs to the document that caused it and to no other.
    """
    services = wired(fail_for={"company/doc03.pdf"})

    many(tmp_path, 8)
    manifest = ingest.MemoryManifest()
    result = ingest.ingest_cycle(services, tmp_path, manifest)

    assert result.failed == 1, f"exactly one document should have failed, {result.failed} did"
    assert any("company/doc03.pdf" in f for f in result.failures), result.failures
    # And the innocent documents must be recorded as ingested rather than dead-lettered alongside it.
    for name in ("company/doc00.pdf", "company/doc04.pdf", "company/doc07.pdf"):
        record = manifest.get(name)
        assert record is not None, f"{name} was not recorded"
        assert not (record.get("failure_count") or 0), f"{name} carries a failure it did not cause"


# --------------------------------------------------------------------------- a wrapper must not hide a retry

def test_an_overload_rejection_wrapped_in_a_runtime_error_is_still_transient():
    """**The defect a concurrent run exposed, and it dead-lettered healthy documents.**

    `embed_texts` tries several endpoints and, when all of them fail, raises a `RuntimeError` carrying the
    last error in its message. `is_transient` classified by looking at the exception's *type*, saw a plain
    `RuntimeError`, and called it a permanent fault of the document. So a `429 Too Many Requests` — the
    embedding service shedding load, which is what it does when a box is busy — was recorded against the
    file. Four documents were dead-lettered that way during a run whose own concurrency provoked the
    rejection, and they had nothing wrong with them at all.

    A wrapper that hides the type it is describing turns a retry into a permanent loss.
    """
    import urllib.error

    inner = urllib.error.HTTPError("http://embeddings:80/embed", 429, "Too Many Requests", {}, None)
    try:
        try:
            raise inner
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"no embedding endpoint answered. Last error: {exc}") from exc
    except RuntimeError as wrapped:
        assert ingest.is_transient(wrapped), (
            "a wrapped 429 is still a load rejection, and reading it as permanent dead-letters a document "
            "that has nothing wrong with it")

    # An implicit context counts too, because `raise ... from` is not the only way a cause is attached.
    try:
        try:
            raise inner
        except urllib.error.HTTPError:
            raise RuntimeError("wrapped without an explicit cause")
    except RuntimeError as chained:
        assert ingest.is_transient(chained), "the implicit context was not followed"


def test_a_genuine_document_fault_is_still_permanent():
    """The other direction, because a classifier that says yes to everything retries a corrupt file forever."""
    assert not ingest.is_transient(RuntimeError("the parser returned a document with no retrievable text"))
    assert not ingest.is_transient(ValueError("not a document"))


def test_a_wrapped_404_is_not_retried():
    """Only the statuses worth retrying. A missing endpoint is a configuration fault, not a busy one."""
    import urllib.error

    inner = urllib.error.HTTPError("http://parser:5001/parse", 404, "Not Found", {}, None)
    try:
        try:
            raise inner
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"wrapped: {exc}") from exc
    except RuntimeError as wrapped:
        assert not ingest.is_transient(wrapped)


# ---------------------------------------------------------------- the tokenizer batches for the post-condition

def test_the_tokenizer_splits_batches_under_the_services_cap(monkeypatch):
    """TEI refuses a `/tokenize` request over `--max-batch-tokens` with a 422, and the worker measures a whole
    document at once. The measurement has to split the list, and a refusal has to halve rather than fail the
    document, or the documents with the most chunks — the ones the post-condition exists for — are the ones
    that cannot be ingested."""
    calls: list[list[int]] = []

    def fake_post(url, body, timeout, method="POST"):
        texts = body["inputs"]
        calls.append([len(text) for text in texts])
        if len(texts) > 5:
            raise urllib.error.HTTPError(url, 422, "Unprocessable Entity", {}, None)
        return [[{"id": 0} for _ in range(len(text) // 4)] for text in texts]

    monkeypatch.setattr(ingest, "_post", fake_post)
    texts = ["y" * 800 for _ in range(20)]
    counts = ingest.count_tokens(ingest.Services(), texts)
    assert counts == [200] * 20
    first_calls = list(calls)
    # Short texts must not bypass the byte budget: hundreds fit in 7,000 bytes and the service caps inputs too.
    monkeypatch.setattr(ingest, "TOKENIZE_BATCH_TEXTS", 4)
    calls.clear()
    counts = ingest.count_tokens(ingest.Services(), ["short"] * 10)
    assert len(counts) == 10
    assert all(len(call) <= 4 for call in calls)
    # Every text is tokenized exactly once by a request that succeeded; the refused attempts are retried.
    assert sum(len(call) for call in first_calls if len(call) <= 5) == 20
    assert any(len(call) > 5 for call in first_calls), "the simulated refusal has to be exercised"


def test_a_single_chunk_the_service_cannot_tokenize_fails_loudly(monkeypatch):
    """One chunk past the model's limit with truncation off is the invariant violated, and it has to name that
    rather than read as a document parsing problem."""
    def always_422(url, body, timeout, method="POST"):
        raise urllib.error.HTTPError(url, 422, "Unprocessable Entity", {}, None)

    monkeypatch.setattr(ingest, "_post", always_422)
    with pytest.raises(RuntimeError, match="single chunk"):
        ingest.count_tokens(ingest.Services(), ["a" * 10])


def test_embedding_batches_are_bounded_by_tokens_as_well_as_count(monkeypatch):
    """The service caps a request by tokens, and sixteen measured chunks can still exceed it. The batch is
    chosen from the measured counts, so the request never crosses the cap and a document does not fail one
    batch at a time."""
    class FakeChunk:
        def __init__(self, tokens: int) -> None:
            self.text = "x"
            self.token_count = tokens

    monkeypatch.setattr(ingest, "EMBED_BATCH", 16)
    monkeypatch.setattr(ingest, "EMBED_TOKEN_BUDGET", 1000)
    batches = list(ingest.embedding_batches([FakeChunk(400) for _ in range(6)]))
    assert [len(batch) for batch in batches] == [2, 2, 2]
    assert sum(len(batch) for batch in batches) == 6


def test_an_embedding_batch_over_the_service_cap_is_halved(monkeypatch):
    """A 422 on an embedding request means the batch is too large, not that the document is bad."""
    calls: list[int] = []

    def fake_post(url, body, timeout, method="POST"):
        texts = body["inputs"]
        calls.append(len(texts))
        if len(texts) > 2:
            raise urllib.error.HTTPError(url, 422, "Unprocessable Entity", {}, None)
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(ingest, "_post", fake_post)
    vectors = ingest.embed_texts(ingest.Services(), ["a", "b", "c", "d", "e"])
    assert len(vectors) == 5
    assert any(size > 2 for size in calls), "the refusal has to be exercised"
