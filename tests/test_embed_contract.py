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
        "chunker_version": "chunker-v1",
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


def test_the_contract_module_is_in_the_image_that_imports_it():
    """Found by deploying it: the worker refused to run with "No module named 'contract'", because the
    Dockerfile copies a fixed file list and this module was not on it.

    The unit tests passed, because they import from the source tree where every file is present. The image is a
    different set of files and nothing in the test suite was checking that the set was complete, which makes this
    the same class of fault as a check that passes while the thing it guards is not running.
    """
    dockerfile = (ROOT / "services" / "ingestion" / "Dockerfile").read_text()
    copy_lines = [line for line in dockerfile.splitlines() if line.startswith("COPY")]
    assert copy_lines, "the Dockerfile stopped copying anything, so this test is stale"
    copied = " ".join(copy_lines)
    for module in ("chunker.py", "ingest.py", "identifiers.py", "contract.py"):
        assert module in copied, f"{module} is imported by the worker and not copied into its image"

    # And every local module the worker imports is on the list, so a module added later fails here rather than
    # at run time on the box.
    import re
    source = (ROOT / "services" / "ingestion" / "ingest.py").read_text()
    local_imports = set(re.findall(r"^(?:import|from) (\w+)(?: import|$)", source, re.M))
    for name in sorted(local_imports):
        if (ROOT / "services" / "ingestion" / f"{name}.py").exists():
            assert f"{name}.py" in copied, (
                f"ingest.py imports {name} from the same directory and the image does not copy it")


def test_the_contract_holds_nothing_the_checking_process_computes_about_itself():
    """The deployed failure, as a test.

    The writer measured the dimension and remembered it on its own Services object; the reader had never
    measured it, so its contract said None. The writer had not yet discovered which batch endpoint answers and
    recorded None; the reader had discovered /embed. Two processes that agreed completely about the model
    produced contracts that differed, and the check refused EVERY search on the deployment.

    The eval harness reported all ten questions as misses, which is what a contract that refuses everything
    looks like from one step up. The lesson is that a contract containing anything the checking process computes
    about itself will differ between two processes that agree about the thing being checked.
    """
    fields = set(contract.CONTRACT_FIELDS)
    assert "embed_path" not in fields, (
        "the endpoint a client reached is how it talks to the service, not what the service is; two endpoints "
        "serving different models differ in model_id")
    # dimension stays, because it is a real property of the model, and it is passed in rather than read off the
    # object so that a process which has never embedded anything still states it.
    assert "dimension" in fields
    signature = __import__("inspect").signature(contract.live_contract)
    assert "dimension" in signature.parameters, (
        "the dimension is read off the Services object again, which is what made a reader that had never "
        "embedded anything disagree with a writer that had")

    # And the reader measures it rather than trusting a field it may never have set.
    source = (ROOT / "services" / "retrieval" / "retriever.py").read_text()
    assert "ingest._get_vector_size(services)" in source


def test_a_contract_that_differs_in_no_field_that_matters_is_not_a_mismatch():
    """The other half: two contracts built by different processes for the same deployment must compare equal."""
    writer = sample()
    reader = sample()
    assert contract.compare(writer, reader) == []
    assert contract.fingerprint(writer) == contract.fingerprint(reader)


def test_a_field_that_is_absent_and_a_field_that_is_null_are_different():
    """The deployed failure, in its second form.

    The comparison used `.get(field)` on both sides, so a field present with a null value and a field missing
    entirely both read as None and compared equal, while the fingerprints over the two dictionaries differed.
    The result was a refusal reading "the difference is ... : ." with an empty list: a message naming nothing,
    because the comparison could not see what the fingerprint could.

    It is the same distinction the module already makes about model_sha, where a server reporting null and a
    server saying nothing are recorded differently. Writing it twice in one file is what made it worth a test.
    """
    # Built by adding and removing a key rather than by overriding one, because the field is no longer in the
    # contract at all: the point is a dictionary that HAS a key with a null value against one that does not
    # have the key.
    recorded = sample()
    recorded["some_field"] = None
    live = sample()
    differences = contract.compare(recorded, live)
    assert len(differences) == 1
    assert differences[0].field == "some_field"
    assert differences[0].live == contract.ABSENT
    assert differences[0].recorded is None
    # And the fingerprint agrees with the comparison, which is the property that was broken.
    assert contract.fingerprint(recorded) != contract.fingerprint(live)


def test_two_nulls_compare_equal():
    """The other direction, so the fix does not turn every null into a difference and refuse on a contract
    nobody changed. `model_sha` is null on this build and stays null."""
    assert contract.compare(sample(), sample()) == []
    assert contract.compare(sample(model_sha=None), sample(model_sha=None)) == []


def test_a_shape_change_is_told_apart_from_a_content_change():
    """A field added to or removed from the contract changes its description, not the vectors. Re-recording is
    the correction, and starting a generation for it would say the vectors were built under a different
    contract, which is false."""
    schema_only = contract.compare(sample(extra_field=1), sample())
    assert contract.only_schema_differences(schema_only)

    content = contract.compare(sample(pooling="cls"), sample(pooling="mean"))
    assert not contract.only_schema_differences(content)

    # A field that was there with a value and is now gone is a shape change; a field that moved is not.
    both = contract.compare(sample(extra_field=1, pooling="cls"), sample(pooling="mean"))
    assert not contract.only_schema_differences(both)


def test_a_bundle_serving_deployment_can_declare_the_chunker_version_in_its_vectors(monkeypatch):
    """The vector store can hold a bundle's chunks while the ingestion worker is on a newer chunker.

    The stored vectors describe the bundle's chunker, so the read contract has to name that version or every
    retrieval is refused. The override is deployment configuration, not a change to the ingestion chunker.
    """
    services = types.SimpleNamespace(embed_url="http://embeddings:80", dimension=1024,
                                     normalise_embeddings=True, query_prefix="", passage_prefix="")
    info = {"model_id": "BAAI/bge-m3", "model_sha": None, "model_dtype": "float32",
            "model_type": {"embedding": {"pooling": "cls"}}, "max_input_length": 8192,
            "auto_truncate": False}
    monkeypatch.setenv("OI_SERVING_CHUNKER_VERSION", "chunker-canonical-v1")
    live = contract.live_contract(services, info=info, dimension=1024)
    assert live["chunker_version"] == "chunker-canonical-v1"

    monkeypatch.delenv("OI_SERVING_CHUNKER_VERSION", raising=False)
    live = contract.live_contract(services, info=info, dimension=1024)
    import chunker
    assert live["chunker_version"] == chunker.CHUNKER_VERSION
