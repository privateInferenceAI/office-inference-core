"""The embedding contract: what built the vectors, and refusing to use them when it changes.

RET-EMBED-001. The failure this guards is quiet, which is why the tests are mostly about the fingerprint
covering everything rather than about the message being printed.

Swap the model, change its pooling, alter a prefix, and every vector already stored came from the old contract.
Similarity between a new query vector and an old passage vector is not zero, it is meaningless, and meaningless
similarity returns results in a plausible order. The store stays green and the answers get subtly worse.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "ingestion"))

# contract.py imports chunker, which is local to the service directory, and nothing else external.
import contract  # noqa: E402


def sample(**overrides) -> dict:
    base = {
        "model_id": "BAAI/bge-m3", "model_sha": None, "model_dtype": "float32", "pooling": "cls",
        "normalise": True, "query_prefix": "", "passage_prefix": "",
        "max_input_length": 4096, "auto_truncate": True, "dimension": 1024,
        "chunker_version": "chunker-v1", "embed_path": "/embed",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- the fingerprint

def test_the_fingerprint_changes_for_every_field_that_changes_a_vector():
    """The list-free property. A comparison that named twelve fields would stop covering the contract the first
    time somebody added a thirteenth, and the failure it leaves is the one this file exists to prevent.

    So every field is perturbed one at a time and each must move the fingerprint.
    """
    base = sample()
    for field in contract.CONTRACT_FIELDS:
        original = base[field]
        if isinstance(original, bool):
            changed = not original
        elif isinstance(original, int):
            changed = original + 1
        elif original is None:
            changed = "something"
        else:
            changed = str(original) + "-changed"
        assert contract.fingerprint(sample(**{field: changed})) != contract.fingerprint(base), (
            f"changing {field} does not change the fingerprint, so a collection built under the old value "
            f"would be searched as though nothing had happened")


def test_a_field_added_to_the_contract_is_covered_without_being_listed():
    """The same property from the other direction: the fingerprint is over the contract as it stands, not over
    the fields somebody remembered to name."""
    assert contract.fingerprint(sample(brand_new_field=1)) != contract.fingerprint(sample())


def test_the_fingerprint_does_not_depend_on_key_order():
    """The contract is built from two sources and assembled in whatever order the code reads, so a fingerprint
    that depended on insertion order would refuse on a change nobody made."""
    a = sample()
    b = {key: a[key] for key in reversed(list(a))}
    assert contract.fingerprint(a) == contract.fingerprint(b)


# --------------------------------------------------------------------------- the comparison

def test_the_comparison_names_the_field_that_moved():
    """A refusal that says two hashes differ is one an operator has to debug. Naming the field is the difference
    between a message and an instruction."""
    recorded = sample()
    live = sample(pooling="mean")
    differences = contract.compare(recorded, live)
    assert len(differences) == 1
    assert differences[0].field == "pooling"
    assert differences[0].recorded == "cls" and differences[0].live == "mean"


def test_the_comparison_covers_a_field_only_one_side_has():
    """Iterated over the union rather than over the field list, so a contract written before a field existed
    shows that field as changed instead of skipping it."""
    differences = contract.compare(sample(), sample(added_later="x"))
    assert [d.field for d in differences] == ["added_later"]


# --------------------------------------------------------------------------- what the service reports

def test_the_contract_is_read_from_the_server_rather_than_from_our_configuration():
    """The thing that matters is what is serving the vectors. A deployment whose model id is right in its
    manifest and wrong in its container is exactly the case this is for."""
    source = (ROOT / "services" / "ingestion" / "contract.py").read_text()
    assert "/info" in source
    assert "model_id" in source and "pooling" in source
    # And it says plainly that a null is recorded rather than dropped, because absent and unknown differ.
    assert "the server does not know" in source or "does not know are different facts" in source


def test_an_unreachable_embedding_service_is_reported_rather_than_read_as_an_empty_contract():
    """An empty contract would fingerprint to something stable and compare equal to a contract that was never
    written, which is the failure mode of a check that cannot tell 'no difference' from 'no answer'."""
    info = contract.read_embedding_service("http://127.0.0.1:1", timeout=1)
    assert "unavailable" in info
    assert "refused" in info["unavailable"].lower() or "error" in info["unavailable"].lower()


# --------------------------------------------------------------------------- the read path

def test_the_read_path_checks_the_contract_before_searching():
    """A change only the writer notices is a reader answering from vectors it should not trust. The writer
    refusing to add to a mismatched collection does nothing about the queries already going to it."""
    source = (ROOT / "services" / "retrieval" / "retriever.py").read_text()
    search = source[source.index("def search("):]
    assert "verify_contract(services)" in search
    # Before the search call, not after it.
    assert search.index("verify_contract(services)") < search.index("raw = _search(services, body)")


def test_the_read_check_is_cached_because_the_answer_cannot_change_mid_process():
    """A question should not pay a database round trip and an HTTP call for a fact that was true at startup, and
    a deployment that swaps its embedding model restarts its readers."""
    # Read rather than imported: the retriever pulls in the ingestion service and a database driver, and what
    # is being asserted is the shape of the check rather than anything it computes.
    source = (ROOT / "services" / "retrieval" / "retriever.py").read_text()
    assert "_CONTRACT_CHECKED: set[str] = set()" in source
    assert "if services.collection in _CONTRACT_CHECKED" in source
    assert "_CONTRACT_CHECKED.add(services.collection)" in source


def test_nothing_re_embeds_the_corpus_automatically():
    """Re-embedding a client's corpus is a cost and a choice, and a script that did it on noticing a mismatch
    would do it at whatever moment the mismatch was noticed, which could be during a search."""
    source = (ROOT / "services" / "ingestion" / "ingest.py").read_text()
    assert "EMBED_CONTRACT_ALLOW_CHANGE" in source
    # The default is refusal: the helper returns False unless the variable says otherwise.
    helper = source[source.index("def _contract_change_allowed"):]
    helper = helper[:helper.index("def ", 1)]
    assert 'in ("1", "true", "yes")' in helper, "the opt-in is not an opt-in"
    assert "Never by default" in helper
