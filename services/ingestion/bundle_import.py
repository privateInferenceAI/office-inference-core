"""Import an oi-bundle-v0 corpus bundle instead of parsing and chunking source documents.

This is the lean ingestion path. A bundle from `oi-corpus-builder` is a full snapshot: canonical
documents, deterministic chunks, originals rows, fidelity reports and checksums. The importer verifies
the snapshot against the documents root, maps each chunk to the payload and lexical row the serving
stack expects, embeds it with the same TEI service the product pipeline uses, and writes it to Qdrant
and Postgres. Nothing in this path calls the parser, the chunker, the scanner or the delta manifest
logic: the bundle replaces all four.

The importer is deliberately strict about what it trusts. It checks every checksum, every document
identity, every chunk id, every original hash and every cross-store count before writing anything. A
bundle that fails a check is refused as a whole, because importing half a corpus silently is the
failure mode a lean stack cannot afford.

    python3 /app/bundle_import.py --bundle /bundle --documents /documents --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import contract
from ingest import (
    Services,
    build_manifest,
    delete_document,
    embed_texts,
    embedding_batches,
    ensure_collection,
    index_for_lexical,
    upsert_points,
)

BUNDLE_SCHEMA_VERSION = "oi-bundle-v0"
FORMAT_VERSION = "oi-doc-v0"
CANONICAL_CHUNKER_VERSION = "chunker-canonical-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")
CHUNK_KINDS = frozenset({"prose", "table", "table_row", "table_marker"})
STATUSES = frozenset({"accepted", "rejected"})
EMBEDDING_FIELDS = ("model_id", "model_revision", "dimension", "query_prefix", "passage_prefix",
                    "normalise")


class VerificationError(ValueError):
    """The bundle does not pass its own checks. The message lists every problem found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("bundle verification failed:\n  - " + "\n  - ".join(problems))


@dataclass
class VerifiedBundle:
    """A verified bundle, with its stores parsed and indexed for import."""

    path: Path
    descriptor: dict
    manifest: list[dict]
    chunks: list[dict]
    originals: list[dict]
    fidelity: list[dict]
    chunks_by_doc: dict[str, list[dict]]

    @property
    def build_id(self) -> str:
        return self.descriptor["build_id"]

    @property
    def corpus_id(self) -> str:
        return self.descriptor["corpus_id"]

    @property
    def chunker_version(self) -> str:
        return self.descriptor["chunker_version"]

    @property
    def embedding_contract(self) -> dict:
        return self.descriptor["embedding_contract"]

    def accepted(self) -> list[dict]:
        return [row for row in self.manifest if row["status"] == "accepted"]

    def rejected(self) -> list[dict]:
        return [row for row in self.manifest if row["status"] == "rejected"]


# ------------------------------------------------------------------ verification helpers
def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def path_problem(value: object, label: str) -> str | None:
    """The bundle's path rules: relative, no traversal, no control characters, UTF-8."""
    if not isinstance(value, str) or not value:
        return f"{label} must be a non-empty string"
    if value.startswith("/"):
        return f"{label} must not be absolute"
    if "\\" in value:
        return f"{label} must not contain a backslash"
    if value.endswith("/"):
        return f"{label} must not end with a slash"
    for character in value:
        if ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F:
            return f"{label} must not contain control characters"
        if 0xD800 <= ord(character) <= 0xDFFF:
            return f"{label} must not contain surrogate code points"
    if any(segment in ("", ".", "..") for segment in value.split("/")):
        return f"{label} must not contain empty, . or .. segments"
    return None


def expected_chunk_id(doc_id: str, canonical_sha256: str, chunker_version: str, ordinal: int,
                      text: str) -> str:
    """The spec 7.1 chunk id, recomputed here so the importer does not trust the row."""
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = "|".join((doc_id, canonical_sha256, chunker_version, str(ordinal), text_hash))
    return str(uuid.uuid5(UUID_NAMESPACE, key))


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise VerificationError([f"{path.name} line {number} is not a JSON object"])
        rows.append(value)
    return rows


def _check_descriptor(descriptor: dict, bundle_dir: Path) -> list[str]:
    problems: list[str] = []
    checks = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "format_version": FORMAT_VERSION,
        "chunker_version": CANONICAL_CHUNKER_VERSION,
    }
    for field, expected in checks.items():
        if descriptor.get(field) != expected:
            problems.append(f"{field} must be {expected!r}, found {descriptor.get(field)!r}")
    for field in ("build_id", "created_at", "builder_version", "corpus_id"):
        if not isinstance(descriptor.get(field), str) or not descriptor[field]:
            problems.append(f"{field} must be a non-empty string")
    if not isinstance(descriptor.get("partial"), bool):
        problems.append("partial must be a boolean")
    counts = descriptor.get("counts")
    if not isinstance(counts, dict):
        problems.append("counts must be an object")
    else:
        for field in ("documents_total", "documents_accepted", "documents_rejected", "chunks"):
            if not isinstance(counts.get(field), int) or counts[field] < 0:
                problems.append(f"counts.{field} must be a non-negative integer")
    embedding = descriptor.get("embedding_contract")
    if not isinstance(embedding, dict):
        problems.append("embedding_contract must be an object")
    else:
        for field in EMBEDDING_FIELDS:
            if field not in embedding:
                problems.append(f"embedding_contract.{field} is missing")
        if embedding.get("model_id") != "BAAI/bge-m3":
            problems.append("this importer serves BAAI/bge-m3 and refuses another embedding model")
        if embedding.get("dimension") != 1024:
            problems.append("the embedding contract must declare dimension 1024")
        if embedding.get("query_prefix") != "" or embedding.get("passage_prefix") != "":
            problems.append("this importer expects empty embedding prefixes")
    parameters = descriptor.get("parameters")
    expected_parameters = {
        "prose_max_tokens": 512, "table_max_tokens": 512,
        "max_row_entries": 5000, "max_table_chunks": 200,
    }
    if not isinstance(parameters, dict):
        problems.append("parameters must be an object")
    else:
        for field, expected in expected_parameters.items():
            if parameters.get(field) != expected:
                problems.append(f"parameters.{field} must be {expected}")
    extraction = descriptor.get("extraction")
    if not isinstance(extraction, dict) or not extraction.get("engine") or not extraction.get("version"):
        problems.append("extraction must name the engine and version")
    checksums = descriptor.get("checksums")
    if not isinstance(checksums, dict) or not checksums:
        problems.append("checksums must be a non-empty object")
    else:
        if "bundle.json" in checksums:
            problems.append("checksums must not include bundle.json")
        present = {
            str(path.relative_to(bundle_dir)) for path in bundle_dir.rglob("*") if path.is_file()
        } - {"bundle.json"}
        if set(checksums) != present:
            missing = sorted(present - set(checksums))
            extra = sorted(set(checksums) - present)
            if missing:
                problems.append(f"files not in checksums: {missing[:3]}")
            if extra:
                problems.append(f"checksums name missing files: {extra[:3]}")
        for name, digest in checksums.items():
            path = bundle_dir / name
            if path.is_file() and sha256_file(path) != digest:
                problems.append(f"checksum mismatch for {name}")
    return problems


def _check_manifest(rows: list[dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        doc_id = row.get("doc_id")
        problem = path_problem(doc_id, "doc_id")
        if problem:
            problems.append(problem)
            continue
        if doc_id in seen:
            problems.append(f"duplicate manifest row for {doc_id!r}")
        seen.add(doc_id)
        if row.get("status") not in STATUSES:
            problems.append(f"{doc_id}: status must be accepted or rejected")
        if not isinstance(row.get("source_sha256"), str) or not SHA256_RE.fullmatch(
            row.get("source_sha256") or ""
        ):
            problems.append(f"{doc_id}: source_sha256 is not a SHA-256")
        if not isinstance(row.get("source_bytes"), int) or row["source_bytes"] < 1:
            problems.append(f"{doc_id}: source_bytes must be positive")
        status = row.get("status")
        if status == "accepted":
            if row.get("page_map") not in ("native", "nominal"):
                problems.append(f"{doc_id}: accepted row needs a page map")
            if not isinstance(row.get("page_count"), int) or row["page_count"] < 1:
                problems.append(f"{doc_id}: accepted row needs a page count")
            expected = f"canonical/{doc_id}.md"
            if row.get("canonical_path") != expected:
                problems.append(f"{doc_id}: canonical_path must be {expected!r}")
            if not isinstance(row.get("canonical_sha256"), str) or not SHA256_RE.fullmatch(
                row.get("canonical_sha256") or ""
            ):
                problems.append(f"{doc_id}: canonical_sha256 is not a SHA-256")
            if row.get("fidelity_reason") is not None:
                problems.append(f"{doc_id}: an accepted row must not carry a fidelity reason")
            if not isinstance(row.get("chunk_count"), int) or row["chunk_count"] < 0:
                problems.append(f"{doc_id}: chunk_count must be a non-negative integer")
        elif status == "rejected":
            if row.get("canonical_path") is not None or row.get("canonical_sha256") is not None:
                problems.append(f"{doc_id}: a rejected row carries no canonical document")
            if row.get("chunk_count") != 0:
                problems.append(f"{doc_id}: a rejected row has no chunks")
            if not isinstance(row.get("fidelity_reason"), str) or not row["fidelity_reason"]:
                problems.append(f"{doc_id}: a rejected row must name its reason")
        llm = row.get("llm")
        if not isinstance(llm, dict) or not llm.get("model") or not isinstance(
            llm.get("attempts"), int
        ):
            problems.append(f"{doc_id}: llm facts are incomplete")
    return problems


def _check_originals(rows: list[dict], manifest: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        doc_id = row.get("doc_id")
        problem = path_problem(doc_id, "originals doc_id")
        if problem:
            problems.append(problem)
            continue
        if doc_id in seen:
            problems.append(f"duplicate originals row for {doc_id!r}")
        seen.add(doc_id)
        manifest_row = manifest.get(doc_id)
        if manifest_row is None or manifest_row.get("status") != "accepted":
            problems.append(f"originals row for {doc_id!r} has no accepted manifest row")
            continue
        if row.get("source_path") != doc_id:
            problems.append(f"{doc_id}: source_path must equal doc_id")
        if row.get("source_sha256") != manifest_row.get("source_sha256"):
            problems.append(f"{doc_id}: original sha does not match the manifest")
        if row.get("source_bytes") != manifest_row.get("source_bytes"):
            problems.append(f"{doc_id}: original bytes do not match the manifest")
        if not isinstance(row.get("source_media_type"), str) or "/" not in row["source_media_type"]:
            problems.append(f"{doc_id}: source_media_type is not a media type")
    accepted = {doc_id for doc_id, row in manifest.items() if row.get("status") == "accepted"}
    for doc_id in sorted(accepted - seen):
        problems.append(f"accepted document {doc_id!r} has no originals row")
    return problems


def _check_fidelity(rows: list[dict], manifest: dict[str, dict], descriptor: dict) -> list[str]:
    problems: list[str] = []
    reports = {row.get("doc_id"): row for row in rows}
    if len(reports) != len(rows):
        problems.append("duplicate fidelity row for a document")
    extraction = descriptor.get("extraction") or {}
    for doc_id, manifest_row in manifest.items():
        report = reports.get(doc_id)
        if report is None:
            problems.append(f"document {doc_id!r} has no fidelity row")
            continue
        if report.get("status") != manifest_row.get("status"):
            problems.append(f"{doc_id}: fidelity status does not match the manifest")
        if (report.get("extraction") or {}).get("engine") != extraction.get("engine") or (
            report.get("extraction") or {}
        ).get("version") != extraction.get("version"):
            problems.append(f"{doc_id}: fidelity extraction does not match the bundle")
        checks = report.get("checks")
        if not isinstance(checks, dict) or len(checks) != 6:
            problems.append(f"{doc_id}: fidelity report needs the six checks")
    for doc_id in sorted(set(reports) - set(manifest)):
        problems.append(f"fidelity row for {doc_id!r} has no manifest row")
    return problems


def _check_chunks(rows: list[dict], manifest: dict[str, dict], descriptor: dict) -> list[str]:
    problems: list[str] = []
    chunker_version = descriptor.get("chunker_version")
    per_doc: dict[str, int] = {}
    last_doc: str | None = None
    last_ordinal = -1
    seen_ids: set[str] = set()
    for row in rows:
        doc_id = row.get("doc_id")
        problem = path_problem(doc_id, "chunk doc_id")
        if problem:
            problems.append(problem)
            continue
        if doc_id != last_doc:
            if last_doc is not None and doc_id < last_doc:
                problems.append("chunks must be grouped by document in sorted order")
            last_doc = doc_id
            last_ordinal = -1
        ordinal = row.get("ordinal")
        if not isinstance(ordinal, int) or ordinal != last_ordinal + 1:
            problems.append(f"{doc_id}: chunk ordinals must be contiguous from zero")
        last_ordinal = ordinal if isinstance(ordinal, int) else last_ordinal
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
        manifest_row = manifest.get(doc_id)
        if manifest_row is None or manifest_row.get("status") != "accepted":
            problems.append(f"chunk for {doc_id!r} has no accepted manifest row")
            continue
        if row.get("kind") not in CHUNK_KINDS:
            problems.append(f"{doc_id}: unknown chunk kind {row.get('kind')!r}")
        if not isinstance(row.get("text"), str) or not row["text"]:
            problems.append(f"{doc_id}: chunk text must be non-empty")
            continue
        if not isinstance(row.get("token_count"), int) or row["token_count"] < 1:
            problems.append(f"{doc_id}: chunk token_count must be positive")
        if row.get("canonical_sha256") != manifest_row.get("canonical_sha256"):
            problems.append(f"{doc_id}: chunk canonical hash does not match the manifest")
        source = row.get("source") or {}
        if source.get("sha256") != manifest_row.get("source_sha256"):
            problems.append(f"{doc_id}: chunk source sha does not match the manifest")
        if source.get("bytes") != manifest_row.get("source_bytes"):
            problems.append(f"{doc_id}: chunk source bytes do not match the manifest")
        page_start, page_end = row.get("page_start"), row.get("page_end")
        if not isinstance(page_start, int) or not isinstance(page_end, int) or page_end < page_start:
            problems.append(f"{doc_id}: chunk page range is invalid")
        elif page_end > (manifest_row.get("page_count") or 0):
            problems.append(f"{doc_id}: chunk page range exceeds the page count")
        if source.get("page_start") != page_start or source.get("page_end") != page_end:
            problems.append(f"{doc_id}: chunk source page range must equal the chunk page range")
        expected = expected_chunk_id(
            doc_id, row.get("canonical_sha256") or "", chunker_version or "", ordinal, row["text"]
        )
        if row.get("chunk_id") != expected:
            problems.append(f"{doc_id}: chunk id does not recompute")
        if row.get("chunk_id") in seen_ids:
            problems.append(f"{doc_id}: duplicate chunk id {row.get('chunk_id')!r}")
        seen_ids.add(row.get("chunk_id"))
        if row.get("kind") in ("table", "table_row", "table_marker"):
            table = row.get("table")
            if not isinstance(table, dict) or not isinstance(table.get("table_id"), str):
                problems.append(f"{doc_id}: a table chunk needs a table reference")
            elif not table["table_id"].startswith(doc_id + ":table:"):
                problems.append(f"{doc_id}: table id must start with the document id")
    for doc_id, manifest_row in manifest.items():
        if manifest_row.get("status") != "accepted":
            continue
        if per_doc.get(doc_id, 0) != manifest_row.get("chunk_count"):
            problems.append(f"{doc_id}: chunk_count does not match the chunk rows")
    return problems


def _check_canonical_files(bundle_dir: Path, manifest: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    present = {
        str(path.relative_to(bundle_dir)) for path in (bundle_dir / "canonical").rglob("*")
        if path.is_file()
    } if (bundle_dir / "canonical").is_dir() else set()
    expected = {
        row["canonical_path"] for row in manifest.values() if row.get("status") == "accepted"
    }
    for path in sorted(expected - present):
        problems.append(f"canonical file is missing: {path}")
    for path in sorted(present - expected):
        problems.append(f"canonical file has no accepted manifest row: {path}")
    return problems


def _check_originals_on_disk(bundle: VerifiedBundle, documents_root: Path) -> list[str]:
    problems: list[str] = []
    for row in bundle.originals:
        doc_id = row["doc_id"]
        original = documents_root / doc_id
        if not original.is_file():
            problems.append(f"original is missing under the documents root: {doc_id}")
            continue
        if original.stat().st_size != row["source_bytes"] or sha256_file(original) != row["source_sha256"]:
            problems.append(f"original does not match its bundle row: {doc_id}")
    return problems


def verify_bundle(bundle_dir: Path, documents_root: Path, *,
                  allow_partial: bool = False) -> VerifiedBundle:
    """Verify every store, the checksums, the cross-store links and the originals on disk."""
    bundle_dir = Path(bundle_dir)
    documents_root = Path(documents_root)
    if not (bundle_dir / "bundle.json").is_file():
        raise VerificationError([f"{bundle_dir} is not a bundle: bundle.json is missing"])
    descriptor = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    if not isinstance(descriptor, dict):
        raise VerificationError(["bundle.json must be a JSON object"])
    problems = _check_descriptor(descriptor, bundle_dir)
    if problems:
        raise VerificationError(problems)
    manifest_rows = _read_jsonl(bundle_dir / "manifest.jsonl")
    chunk_rows = _read_jsonl(bundle_dir / "chunks.jsonl")
    original_rows = _read_jsonl(bundle_dir / "originals.jsonl")
    fidelity_rows = _read_jsonl(bundle_dir / "fidelity.jsonl")
    manifest = {row.get("doc_id"): row for row in manifest_rows}
    problems = (
        _check_manifest(manifest_rows)
        + _check_originals(original_rows, manifest)
        + _check_fidelity(fidelity_rows, manifest, descriptor)
        + _check_chunks(chunk_rows, manifest, descriptor)
        + _check_canonical_files(bundle_dir, manifest)
    )
    if problems:
        raise VerificationError(problems)
    bundle = VerifiedBundle(
        path=bundle_dir, descriptor=descriptor, manifest=manifest_rows, chunks=chunk_rows,
        originals=original_rows, fidelity=fidelity_rows,
        chunks_by_doc={},
    )
    problems = _check_originals_on_disk(bundle, documents_root)
    if problems:
        raise VerificationError(problems)
    accepted = sum(1 for row in manifest_rows if row["status"] == "accepted")
    rejected = len(manifest_rows) - accepted
    counts = descriptor["counts"]
    if (counts["documents_total"], counts["documents_accepted"], counts["documents_rejected"],
            counts["chunks"]) != (len(manifest_rows), accepted, rejected, len(chunk_rows)):
        raise VerificationError(["bundle counts do not match the rows"])
    if rejected and not allow_partial:
        raise VerificationError([
            f"the bundle is partial ({rejected} rejected documents); an operator must pass "
            "--allow-partial to import it"
        ])
    for row in chunk_rows:
        bundle.chunks_by_doc.setdefault(row["doc_id"], []).append(row)
    return bundle


# ------------------------------------------------------------------ mapping and import
def document_root(doc_id: str) -> str:
    """The access label: the first component of the document path, as the scanner set it."""
    return doc_id.split("/", 1)[0] if "/" in doc_id else doc_id


def chunk_payload(bundle: VerifiedBundle, manifest_row: dict, chunk: dict) -> dict:
    """One bundle chunk as the Qdrant payload the serving stack reads."""
    table = chunk.get("table") or {}
    headings = chunk.get("heading_path") or []
    if chunk["kind"] == "table":
        # The product row reader strips a leading line equal to `headings`. A readable table chunk's
        # first line is its caption/columns line, so listing that line here keeps row ordinals
        # aligned with the table row range instead of counting the header as row one.
        first_line = chunk["text"].split("\n", 1)[0].strip()
        if first_line:
            headings = [first_line]
    return {
        "chunk_id": chunk["chunk_id"],
        "doc_id": chunk["doc_id"],
        "acl": document_root(chunk["doc_id"]),
        "source_sha256": manifest_row["source_sha256"],
        "chunker_version": bundle.chunker_version,
        "text": chunk["text"],
        "kind": chunk["kind"],
        "page_start": chunk["page_start"],
        "page_end": chunk["page_end"],
        "page_is_nominal": manifest_row["page_map"] == "nominal",
        "table_id": table.get("table_id"),
        "row_index": table.get("row_start") if chunk["kind"] == "table_row" else None,
        "row_start": table.get("row_start"),
        "row_end": table.get("row_end"),
        "table_rows": table.get("row_count"),
        "is_ocr": False,
        "headings": headings,
        "token_count": chunk["token_count"],
    }


def embedding_contract_problems(bundle_contract: dict, live: dict) -> list[str]:
    """The bundle's contract must agree with the embedding service that will store the vectors."""
    problems: list[str] = []
    live_model = live.get("model_id") or ""
    if live_model and live_model != bundle_contract.get("model_id"):
        problems.append(
            f"bundle model {bundle_contract.get('model_id')!r} is not the serving model {live_model!r}"
        )
    if live.get("dimension") not in (None, bundle_contract.get("dimension")):
        problems.append(
            f"bundle dimension {bundle_contract.get('dimension')} is not the serving dimension "
            f"{live.get('dimension')}"
        )
    for field in ("query_prefix", "passage_prefix"):
        if live.get(field, "") != bundle_contract.get(field, ""):
            problems.append(f"embedding {field} differs between the bundle and the serving contract")
    if bool(live.get("normalise")) != bool(bundle_contract.get("normalise")):
        problems.append("embedding normalisation differs between the bundle and the serving contract")
    return problems


def lean_contract(bundle: VerifiedBundle, live: dict) -> dict:
    """The serving contract with the bundle's chunker version, which is what the vectors describe."""
    contract_now = dict(live)
    contract_now["chunker_version"] = bundle.chunker_version
    return contract_now


def plan_import(bundle: VerifiedBundle, existing_doc_ids: set[str]) -> dict:
    """What the import will write and delete. Pure, so a dry run and the real run agree."""
    accepted = [row["doc_id"] for row in bundle.accepted()]
    rejected = [row["doc_id"] for row in bundle.rejected()]
    bundle_ids = set(accepted) | set(rejected)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "stale": sorted(existing_doc_ids - bundle_ids),
        "chunks": sum(len(bundle.chunks_by_doc.get(doc_id, [])) for doc_id in accepted),
        "documents": len(accepted),
    }


def ensure_collection_light(services: Services) -> int:
    """Use an existing collection's measured dimension; only create one when it is absent.

    The product `ensure_collection` re-measures the embedding dimension on every call. With several
    import shards starting together, that probe competes with their embedding batches and a load-shed
    429 is fatal to the shard. An existing collection already states its dimension, so the lean import
    reads it and skips the probe. A missing collection still goes through the product path, which then
    does need the probe.
    """
    import json as _json
    import urllib.request

    url = f"{services.vector_url}/collections/{services.collection}"
    try:
        with urllib.request.urlopen(url, timeout=services.timeout) as response:
            existing = _json.loads(response.read())
    except Exception:  # noqa: BLE001 - absent or unreachable; fall through to the product path
        return ensure_collection(services)
    params = ((existing.get("config") or {}).get("params") or {}).get("vectors") or {}
    size = params.get("size")
    distance = params.get("distance")
    if not isinstance(size, int):
        return ensure_collection(services)
    if distance and distance != "Cosine":
        raise RuntimeError(
            f"collection {services.collection} uses {distance} distance; the lean importer expects Cosine")
    services.dimension = size
    print(f"collection {services.collection}: {size} dimensions, {distance}, "
          f"{existing.get('points_count')} points (existing; dimension probe skipped)")
    return size


def already_imported(bundle: VerifiedBundle, manifest, row: dict) -> bool:
    """Whether the manifest already holds this exact document under this chunker version."""
    record = manifest.get(row["doc_id"])
    if not isinstance(record, dict):
        return False
    return (
        record.get("content_sha256") == row["source_sha256"]
        and record.get("chunker_version") == bundle.chunker_version
        and record.get("chunk_count") == row["chunk_count"]
    )


def import_bundle(bundle: VerifiedBundle, services: Services, manifest, *,
                  dry_run: bool = False, only_doc_ids: set[str] | None = None,
                  remove_stale: bool = True, skip_existing: bool = False) -> dict:
    """Write the bundle into the serving stores. A full snapshot, not a delta of file changes.

    ``only_doc_ids`` imports a subset (used to parallelise the embedding work); such a shard must
    also pass ``remove_stale=False`` because it cannot see the whole bundle. The full importer runs
    once with both defaults.
    """
    existing = set(manifest.all())
    plan = plan_import(bundle, existing)
    if only_doc_ids is not None:
        plan["accepted"] = [doc_id for doc_id in plan["accepted"] if doc_id in only_doc_ids]
        plan["chunks"] = sum(len(bundle.chunks_by_doc.get(doc_id, []))
                             for doc_id in plan["accepted"])
        plan["documents"] = len(plan["accepted"])
    if skip_existing:
        plan["accepted"] = [
            doc_id for doc_id in plan["accepted"]
            if not already_imported(bundle, manifest, next(
                row for row in bundle.accepted() if row["doc_id"] == doc_id))
        ]
        plan["chunks"] = sum(len(bundle.chunks_by_doc.get(doc_id, []))
                             for doc_id in plan["accepted"])
        plan["documents"] = len(plan["accepted"])
    if not remove_stale:
        plan["stale"] = []
    if dry_run:
        return plan
    dimension = ensure_collection_light(services)
    live = contract.live_contract(services, dimension=dimension)
    problems = embedding_contract_problems(bundle.embedding_contract, live)
    if problems:
        raise RuntimeError("bundle embedding contract refused: " + "; ".join(problems))
    lean = lean_contract(bundle, live)
    if hasattr(manifest, "connection"):
        recorded = contract.check(manifest.connection, services.collection, lean)
        print(f"collection contract: {recorded['status']} generation {recorded['generation']}")

    for doc_id in plan["stale"]:
        delete_document(services, doc_id)
        manifest.remove_chunks(doc_id)
        manifest.remove(doc_id)
        print(f"removed stale document {doc_id}")

    imported = 0
    wanted = set(plan["accepted"])
    for manifest_row in bundle.accepted():
        doc_id = manifest_row["doc_id"]
        if doc_id not in wanted:
            continue
        chunks = bundle.chunks_by_doc.get(doc_id, [])
        payloads = [chunk_payload(bundle, manifest_row, chunk) for chunk in chunks]
        wrappers = [
            SimpleNamespace(text=payload["text"], token_count=chunk["token_count"])
            for payload, chunk in zip(payloads, chunks)
        ]
        points: list[dict] = []
        cursor = 0
        for batch in embedding_batches(wrappers):
            vectors = embed_texts(services, [item.text for item in batch])
            if len(vectors) != len(batch):
                raise RuntimeError(
                    f"asked for {len(batch)} vectors for {doc_id} and got {len(vectors)}"
                )
            for item, vector in zip(batch, vectors):
                points.append(
                    {"id": payloads[cursor]["chunk_id"], "vector": vector,
                     "payload": payloads[cursor]}
                )
                cursor += 1
        delete_document(services, doc_id)  # the bundle is authoritative; clear any earlier chunks
        manifest.remove_chunks(doc_id)     # ... including the lexical rows keyed by old chunk ids
        upsert_points(services, points)
        index_for_lexical(manifest, points)
        manifest.put(doc_id, {
            "relpath": doc_id,
            "root": document_root(doc_id),
            "content_sha256": manifest_row["source_sha256"],
            "bytes": manifest_row["source_bytes"],
            "page_count": manifest_row["page_count"],
            "chunk_count": len(points),
            "payload_version": "oi-bundle-v0",
            "chunker_version": bundle.chunker_version,
            "failure_count": 0,
            "failure_kind": None,
            "last_error": None,
        })
        imported += 1
        print(f"imported {doc_id}: {len(points)} chunks")

    for doc_id in (plan["rejected"] if remove_stale else []):
        if doc_id in existing:
            delete_document(services, doc_id)
            manifest.remove_chunks(doc_id)
            manifest.remove(doc_id)
            print(f"removed rejected document {doc_id}")

    return {**plan, "imported": imported}


# ------------------------------------------------------------------ command line
def _services(args: argparse.Namespace) -> Services:
    return Services(
        embed_url=args.embed_url, vector_url=args.vector_url, collection=args.collection,
        database_url=args.database_url,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="bundle directory")
    parser.add_argument("--documents", type=Path,
                        default=Path(os.environ.get("OI_DOCUMENTS_ROOT", "/documents")),
                        help="read-only documents root the originals live under")
    parser.add_argument("--allow-partial", action="store_true",
                        help="import a bundle that contains rejected documents")
    parser.add_argument("--dry-run", action="store_true",
                        help="verify the bundle and print the import plan without writing")
    parser.add_argument("--embed-url", default=os.environ.get("EMBED_URL", "http://embeddings:80"))
    parser.add_argument("--vector-url", default=os.environ.get("VECTOR_URL", "http://vector:6333"))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--collection", default=os.environ.get("COLLECTION", "documents"))
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--skip-stale", action="store_true",
                        help="do not delete documents absent from the bundle (used by import shards)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="do not re-embed documents already imported under this bundle's chunker")
    parser.add_argument("--only", default=None,
                        help="comma-separated document ids to import; implies --skip-stale")
    args = parser.parse_args(argv)
    try:
        bundle = verify_bundle(args.bundle, args.documents, allow_partial=args.allow_partial)
    except VerificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(
        f"verified bundle {bundle.build_id} ({bundle.corpus_id}): "
        f"{len(bundle.accepted())} accepted, {len(bundle.rejected())} rejected, "
        f"{len(bundle.chunks)} chunks"
    )
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("shard-index must be in [0, shard-count)")
    accepted = [row["doc_id"] for row in bundle.accepted()]
    selected = {
        doc_id for index, doc_id in enumerate(sorted(accepted))
        if index % args.shard_count == args.shard_index
    }
    explicit_only = (
        {doc_id.strip() for doc_id in args.only.split(",") if doc_id.strip()}
        if args.only else None
    )
    if explicit_only is not None:
        unknown = explicit_only - set(accepted)
        if unknown:
            parser.error(f"--only names documents that are not accepted in the bundle: {sorted(unknown)}")
    manifest = build_manifest(args.database_url)
    try:
        result = import_bundle(
            bundle, _services(args), manifest, dry_run=args.dry_run,
            only_doc_ids=explicit_only if explicit_only is not None
            else (selected if args.shard_count > 1 else None),
            remove_stale=not args.skip_stale and explicit_only is None,
            skip_existing=args.skip_existing,
        )
    finally:
        if hasattr(manifest, "connection"):
            manifest.connection.close()
    verb = "would import" if args.dry_run else "imported"
    print(
        f"{verb} {result.get('documents', len(bundle.accepted()))} documents, "
        f"{result.get('chunks', len(bundle.chunks))} chunks; stale removed: {len(result['stale'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
