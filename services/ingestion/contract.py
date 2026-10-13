"""The embedding contract: what built the vectors, and refusing to search when it changes.

RET-EMBED-001. The failure is quiet, which is why it needs a mechanism rather than attention.

Swap the embedding model, change its pooling, alter the query prefix, or move to a different revision, and every
vector already in the collection came from the old contract. **Similarity between a new query vector and an old
passage vector is not zero, it is meaningless**, and meaningless similarity still returns results in a plausible
order. The collection stays green, searches return passages, and the only symptom is that the answers are
subtly worse, which nothing on the box measures.

The dimension check in `ensure_collection` catches one field of this and was written when dimension was the only
one anybody changed. **It is the field that fails loudly**, which is why it is the one that got caught: a
dimension mismatch rejects every write. The others fail silently, and a silent failure in a retrieval system
looks exactly like a hard question.

**The comparison is on a fingerprint rather than field by field.** A check that names twelve fields is a list
that stops covering the contract the first time somebody adds a thirteenth, and the failure it leaves is the one
this file exists to prevent. The fingerprint is over the canonical form of every field, and the refusal names
the field that moved by comparing the two contracts rather than only their hashes.

**A change is a decision, not a repair.** Nothing here re-embeds anything automatically. Re-embedding a client's
corpus is a cost and a choice, and a script that did it on noticing a mismatch would do it during a search.
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import dataclass

# The fields that change what a vector means. Named here so the contract has one definition, and the failure
# mode of forgetting one is covered by the fingerprint rather than by this list being complete: a field read
# into the contract is in the fingerprint whether or not anybody remembered to add it to a comparison.
CONTRACT_FIELDS = (
    "model_id",          # which model produced the vectors
    "model_sha",         # which revision of it, when the server knows
    "model_dtype",       # the precision the weights were served at
    "pooling",           # how token vectors became one vector per passage
    "normalise",         # whether vectors are unit length, which changes what cosine means
    "query_prefix",      # text prepended to a question
    "passage_prefix",    # text prepended to a chunk
    "max_input_length",  # where the server starts truncating instead of embedding
    "auto_truncate",     # whether it truncates silently
    "dimension",         # the size, which is also checked in the collection itself
    "chunker_version",   # the chunk boundaries the vectors describe
)

# Two fields were in this contract and are not, and the deployed run is what said so. Both are DISCOVERY STATE
# belonging to the process that happened to be running rather than properties of the vectors, so the writer's
# contract and the reader's described the same model and still differed:
#
#   dimension   the writer measured 1024 and remembered it on its own Services object; the reader had never
#               measured it, so its contract said None and every search was refused as a mismatch.
#   embed_path  the writer had not yet discovered which batch endpoint answers and recorded None; the reader had
#               discovered /embed. The endpoint is how a client reaches the service, not what the service is, and
#               two endpoints serving two different models differ in model_id rather than in their path.
#
# The lesson is worth keeping in the file rather than only in the commit. A contract that includes anything the
# checking process computes about itself will differ between two processes that agree about the thing being
# checked, and the failure is not a near miss: it refused EVERY search on the deployment, and the eval harness
# reported all ten questions as misses before anybody read the reason.


@dataclass
class Mismatch:
    field: str
    recorded: object
    live: object


def canonical(contract: dict) -> str:
    return json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(contract: dict) -> str:
    return hashlib.sha256(canonical(contract).encode("utf-8")).hexdigest()


def read_embedding_service(embed_url: str, timeout: int = 15) -> dict:
    """What the embedding server says about itself.

    `/info` is the authority rather than our configuration, because the thing that matters is what is serving
    the vectors and not what we wrote down. A deployment whose model id is correct in its manifest and wrong in
    its container is exactly the case this is for.

    `model_sha` comes back null on this build and is recorded as null rather than omitted. A field that is
    absent and a field the server does not know are different facts, and only one of them means the revision is
    unverifiable.
    """
    try:
        with urllib.request.urlopen(f"{embed_url.rstrip('/')}/info", timeout=timeout) as response:
            info = json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        return {"unavailable": f"{type(exc).__name__}: {exc}"}
    return info


def live_contract(services, info: dict | None = None, dimension: int | None = None) -> dict:
    """The contract in force now, from the server and from our own configuration.

    The prefixes and the chunker version are ours rather than the server's, and they belong in the same
    fingerprint because a change to either makes the stored vectors mean something different just as surely as a
    model swap does.
    """
    import chunker
    info = read_embedding_service(services.embed_url) if info is None else info
    model_type = (info.get("model_type") or {}).get("embedding") or {}
    return {
        "model_id": info.get("model_id") or info.get("served_model_name"),
        "model_sha": info.get("model_sha"),
        "model_dtype": info.get("model_dtype"),
        "pooling": model_type.get("pooling"),
        "normalise": getattr(services, "normalise_embeddings", True),
        "query_prefix": getattr(services, "query_prefix", ""),
        "passage_prefix": getattr(services, "passage_prefix", ""),
        "max_input_length": info.get("max_input_length"),
        "auto_truncate": info.get("auto_truncate"),
        # Measured, not read off the object. A process that has not embedded anything yet has never measured it,
        # and reading an unmeasured field is what made the reader's contract differ from the writer's.
        "dimension": dimension or getattr(services, "dimension", None),
        "chunker_version": chunker.CHUNKER_VERSION,
    }


def compare(recorded: dict, live: dict) -> list[Mismatch]:
    """Every field that moved, so a refusal can say which rather than that two hashes differ.

    Iterated over the union of both contracts rather than over `CONTRACT_FIELDS`, so a field added to one side
    and not the other shows up as a difference instead of being skipped. That is the same reasoning as the
    fingerprint: the list is not what makes this safe.
    """
    out: list[Mismatch] = []
    for field in sorted(set(recorded) | set(live)):
        if recorded.get(field) != live.get(field):
            out.append(Mismatch(field, recorded.get(field), live.get(field)))
    return out


def check(connection, collection: str, live: dict, allow_change: bool = False,
          reason: str | None = None) -> dict:
    """Compare the live contract with the recorded one, and refuse when they differ.

    Called by the writer before it records and by the reader before it searches, because a contract change that
    only the writer notices is a reader answering from vectors it should not trust.

    `allow_change` starts a new generation. It is a deliberate act and it carries a reason, because the boundary
    exists so that somebody decided rather than so that a check could be satisfied.
    """
    live_fp = fingerprint(live)
    with connection.cursor() as cur:
        cur.execute("select contract, fingerprint, generation from collection_contracts where collection = %s",
                    (collection,))
        row = cur.fetchone()

    if row is None:
        with connection.cursor() as cur:
            cur.execute("""insert into collection_contracts (collection, contract, fingerprint)
                           values (%s, %s, %s)""", (collection, json.dumps(live, sort_keys=True), live_fp))
            cur.execute("""insert into collection_contract_history (collection, contract, fingerprint,
                                                                    generation, reason)
                           values (%s, %s, %s, 1, %s)""",
                        (collection, json.dumps(live, sort_keys=True), live_fp, "recorded at first use"))
        return {"status": "recorded", "fingerprint": live_fp, "generation": 1}

    recorded, recorded_fp, generation = row[0], row[1], row[2]
    if recorded_fp == live_fp:
        return {"status": "unchanged", "fingerprint": live_fp, "generation": generation}

    differences = compare(recorded, live)
    if allow_change:
        with connection.cursor() as cur:
            cur.execute("""update collection_contracts set contract = %s, fingerprint = %s,
                                                           generation = %s, recorded_at = now()
                           where collection = %s""",
                        (json.dumps(live, sort_keys=True), live_fp, generation + 1, collection))
            cur.execute("""insert into collection_contract_history (collection, contract, fingerprint,
                                                                    generation, reason)
                           values (%s, %s, %s, %s, %s)""",
                        (collection, json.dumps(live, sort_keys=True), live_fp, generation + 1, reason))
        return {"status": "changed", "fingerprint": live_fp, "generation": generation + 1,
                "differences": [(m.field, m.recorded, m.live) for m in differences]}

    named = "; ".join(f"{m.field}: {m.recorded!r} -> {m.live!r}" for m in differences)
    raise RuntimeError(
        f"collection {collection} was built under a different embedding contract and the difference is not "
        f"only in its size: {named}. Every vector in it came from the old contract, and similarity between a "
        f"new query vector and an old passage vector is not zero, it is meaningless: searches would return "
        f"passages in a plausible order and the answers would be quietly worse. This is a decision rather than "
        f"a repair. Re-embed the corpus under the new contract, or restore the old one.")
