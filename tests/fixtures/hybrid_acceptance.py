#!/usr/bin/env python3
"""The acceptance test for hybrid search, on the deployed store: hybrid on against hybrid off.

RET-HYBRID-001 asks for a question containing an exact reference that succeeds with the lexical arm and fails
without it. Getting that test right needed a correction to the measurement that opened the step.

The first measurement used one phrasing per reference and concluded that the vector arm misses rare
references. Two probes then disagreed about the same reference, which turned out to be because they asked
different questions: "What does Pub. 515 cover?" is found at rank 7 by the vector arm, and "What is in Pub.
515?" is not found at all. So the finding is about PHRASING rather than rarity, and the corrected measurement
asks each reference five ways.

What it shows is stronger than the original claim:

    reference      bare form     "tell me about"    best phrasing
    Pub. 515       MISSED        MISSED             #5
    Form W-3SS     MISSED        MISSED             #0
    Pub. 519       MISSED        MISSED             #3

The bare reference is missed every single time, and that is the form a person uses when they are looking
something up: they type the thing they want. So this test asks the bare form, because a test built on a
phrasing the vector arm happens to handle would prove nothing.

Hybrid off is simulated by pointing the lexical arm at an empty result, which is what the module does when it
cannot reach Postgres. Using the real code path means the comparison exercises the same fusion, filters and
passage construction a deployment does.

Run from the repo root on the box:

    cd /opt/oi-core
    docker run --rm --network ai-net --user "$(id -u):$(id -g)" \\
      -v /opt/oi-core:/w:ro -v /tmp:/probe:ro -w /w \\
      --entrypoint python oi-core/ingestion:local /tmp/hybrid_acceptance.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, "/w/services/retrieval")
sys.path.insert(0, "/w/services/ingestion")

ENV_PATH = Path("/w/rendered/env")


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    """The deployment's rendered environment.

    rendered/env holds the database password and is mode 600 owned by the operator, so this container has to
    run as that user. The PermissionError case says which flag to add rather than printing a traceback, since
    the fix is a docker flag and not a code change.
    """
    try:
        text = path.read_text()
    except PermissionError:
        print(f"cannot read {path}: it holds the database password, so it is mode 600 owned by the operator\n"
              f"while this container runs as another user. Run the probe as the file's owner:\n\n"
              f"    --user \"$(id -u):$(id -g)\"\n")
        return {}
    except OSError as exc:
        print(f"cannot read {path}: {exc}")
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def database_url() -> str:
    """The DSN, assembled the way compose assembles it.

    rendered/env has no DATABASE_URL: it holds POSTGRES_USER, POSTGRES_PASSWORD and POSTGRES_DB, and the
    compose file builds the connection string from those at run time. Each part is quoted because a generated
    password is hex and safe and a client supplying their own is not obliged to keep it that way.
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = read_env()
    if env.get("DATABASE_URL"):
        print("DATABASE_URL taken from rendered/env")
        return env["DATABASE_URL"]
    if not all(env.get(key) for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")):
        print("rendered/env has no DATABASE_URL and not all of POSTGRES_USER, POSTGRES_PASSWORD and "
              "POSTGRES_DB either, so there is nothing to connect with")
        return ""
    from urllib.parse import quote
    dsn = (f"postgresql://{quote(env['POSTGRES_USER'], safe='')}:"
           f"{quote(env['POSTGRES_PASSWORD'], safe='')}@postgres:5432/{env['POSTGRES_DB']}")
    print("DATABASE_URL assembled from rendered/env the way compose does")
    os.environ["DATABASE_URL"] = dsn
    return dsn


database_url()

import ingest  # noqa: E402
import identifiers  # noqa: E402
import retriever  # noqa: E402

# The bare reference, which is the form a person uses and the form the measurement shows the vector arm
# missing every time. The fourth column is what the lexical extractor should produce from it.
CASES = [
    ("Pub. 515", True),
    ("Form W-3SS", True),
    ("Pub. 519", True),
    ("Form W-4", False),      # common enough that the vector arm handles the bare form too
    ("Pub. 15", False),
]


def count_lexical_rows(services: ingest.Services) -> int:
    import psycopg2
    with psycopg2.connect(services.database_url) as connection:
        with connection.cursor() as cur:
            cur.execute("select count(*), count(*) filter (where identifiers <> '{}') from chunk_index")
            total, with_ids = cur.fetchone()
    print(f"chunk_index: {total} rows, {with_ids} carrying identifiers")
    return total


def main() -> int:
    services = ingest.Services()
    problems: list[str] = []
    if not services.database_url:
        print("no database connection, so the lexical arm cannot run and nothing below would mean anything")
        return 1
    if count_lexical_rows(services) == 0:
        print(
            "chunk_index is empty. Every case below would fail for that reason and not for a retrieval one.\n"
            "The usual cause is a corpus ingested before this store existed: the worker skips a document whose\n"
            "bytes and payload rules are unchanged, so a new write path needs PAYLOAD_VERSION bumped before it\n"
            "reaches anything. Check `docker logs ingestion` for the cycle that followed the migration.")
        return 1

    print()
    print(f"{'reference':<13}{'tokens':<26}{'hybrid on':>10}{'hybrid off':>12}  verdict")
    for reference, must_need_lexical in CASES:
        tokens = identifiers.extract(reference)
        on = retriever.search(services, reference, role="staff", limit=10)
        on_hit = any(reference.lower() in p.text.lower() for p in on.passages)

        original = retriever.lexical_search
        try:
            retriever.lexical_search = lambda *a, **k: []
            off = retriever.search(services, reference, role="staff", limit=10)
        finally:
            retriever.lexical_search = original
        off_hit = any(reference.lower() in p.text.lower() for p in off.passages)

        if must_need_lexical:
            # The acceptance condition: the lexical arm has to be what makes the difference, not the fusion
            # alone and not a phrasing the vector arm happens to handle.
            ok = on_hit and not off_hit
            verdict = "HYBRID WINS" if ok else (
                "no difference" if on_hit == off_hit else "lexical arm hurt")
            if not ok:
                problems.append(
                    f"{reference}: expected hybrid on to find it and hybrid off to miss it, got "
                    f"on={on_hit} off={off_hit}. The vector arm handles this phrasing, so this reference "
                    f"does not test the lexical arm.")
        else:
            ok = on_hit
            verdict = "both find it, as expected" if ok else "REGRESSION: on misses what off found"
            if not ok:
                problems.append(f"{reference}: hybrid on should still find what the vector arm found")
        print(f"{reference:<13}{str(tokens):<26}{str(on_hit):>10}{str(off_hit):>12}  {verdict}")
        print(f"    arms on: {on.arms}")

    print()
    if problems:
        print(f"FAILED with {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASSED: a bare reference is found with the lexical arm and missed without it, and the fusion keeps "
          "what the vector arm already found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
