"""Tests for the compose file itself.

Two of these encode failures that actually happened, which is why they are tests rather than review
notes. The others encode standing rules from the runbook that are easy to break by accident.

Run: python3 -m pytest tests/
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "office" / "compose" / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


@pytest.fixture(scope="module")
def services(compose) -> dict:
    return compose["services"]


def test_our_migrations_are_not_run_by_the_postgres_entrypoint(services):
    """The defect that cost an afternoon.

    Mounting db/migrations at /docker-entrypoint-initdb.d creates our audit tables in the same
    database the gateway owns. Prisma then refuses to baseline a schema it did not create, and
    litellm restart-loops. It only fires on a virgin data directory, so it hits fresh installs and
    nobody's dev box that was built before db/ existed. `audit migrate` is the only path.
    """
    mounts = services["postgres"].get("volumes", [])
    offenders = [m for m in mounts if "docker-entrypoint-initdb.d" in str(m)]
    assert not offenders, (
        "postgres must not run our migrations at initdb: the gateway keeps its Prisma schema in the "
        f"same database and will refuse to start. Found: {offenders}"
    )


def test_a_cloud_build_does_not_start_the_local_lane(services):
    """llamacpp belongs to the office build, and it must be excluded by profile rather than by
    remembering. The renderer sets COMPOSE_PROFILES from the manifest's profile."""
    assert services["llamacpp"]["profiles"] == ["office"]


def test_the_gateway_does_not_depend_on_the_local_lane(services):
    """Compose rejects a project whose dependency sits in an inactive profile, which made the cloud
    build invalid: "service litellm depends on undefined service llamacpp"."""
    depends = services["litellm"].get("depends_on") or {}
    assert "llamacpp" not in depends


def test_nothing_publishes_a_port(services):
    """Rule 4 of the standing rules: nothing publishes a port except ingress. The security group is
    not the control here, because Docker writes its own iptables rules and bypasses UFW."""
    published = {name: svc["ports"] for name, svc in services.items() if svc.get("ports")}
    assert not published, f"these services publish ports: {published}"


def test_third_party_images_are_pinned_by_digest(services):
    """Digest pins, with the version recorded beside the pin. The version comment is checked too:
    an earlier stack pinned a gateway by digest with no version written down, and nobody could
    answer the first question a security review asks.

    Beside or above: llamacpp records its version inline and litellm records it in the comment block
    over the pin, which is where the CVE note lives. Both satisfy the rule.
    """
    lines = COMPOSE.read_text().splitlines()
    version_comment = re.compile(r"#.*(pinned|\bv?\d+\.\d+)")
    for name, svc in services.items():
        image = str(svc.get("image", ""))
        if not image.startswith("ghcr.io/"):
            continue
        assert "@sha256:" in image, f"{name} pulls {image} without a digest pin"
        index = next((i for i, line in enumerate(lines) if image in line), None)
        assert index is not None, f"could not find the image line for {name}"
        window = lines[max(0, index - 8): index + 1]
        assert any(version_comment.search(line) for line in window), (
            f"{name} is digest-pinned with no version recorded beside or above the pin"
        )


def test_the_document_services_are_pinned_and_unpublished(compose):
    """The C2 containers. Every one of them is digest-pinned, on ai-net, and publishes no port: the
    posture is that nothing of ours is reachable from outside the machine.

    `parser` is a local build now, so it pins by its Dockerfile's `FROM` rather than by an `image:` digest.
    That is a weaker check than the others and it is checked separately below, because a build with an
    unpinned base is an unpinned image wearing a tag.
    """
    services = compose["services"]
    for name in ("vector", "embeddings", "reranker"):
        service = services[name]
        assert "sha256:" in service["image"], f"{name} must be digest-pinned"
        assert "ai-net" in service["networks"], f"{name} must be on the private network"
        assert not service.get("ports"), f"{name} must not publish a port"

    parser = services["parser"]
    assert "ai-net" in parser["networks"], "parser must be on the private network"
    assert not parser.get("ports"), "parser must not publish a port"
    assert parser.get("build"), "parser is a local build"
    dockerfile = (COMPOSE.parents[3] / "services" / "parser" / "Dockerfile").read_text()
    assert "sha256:" in dockerfile, (
        "the parser is a local build, so its base image must be digest-pinned in the Dockerfile; a tag "
        "would be a different image next month")


def test_the_parser_service_refuses_options_it_does_not_understand():
    """The service that replaced docling-serve exists to stop a class of silent failure, so its own
    handling of an unknown option is the thing to check.

    docling-serve ignores an unknown field while answering success. `do_ocr`, `do_table_structure` and
    `table_mode` all looked like they worked for three round trips because of it. The replacement refuses
    the request and names what it accepts, and that behaviour is in the source rather than in a comment.
    """
    source = (COMPOSE.parents[3] / "services" / "parser" / "parser_service.py").read_text()
    assert "ACCEPTED_FIELDS" in source
    assert "unknown option(s)" in source, "an unknown option must be reported, not ignored"
    # And the fields the worker sends must be in the accepted set, or every document fails.
    worker = (COMPOSE.parents[3] / "services" / "ingestion" / "ingest.py").read_text()
    for field in ("to_formats", "image_export_mode", "ocr", "table_structure"):
        assert f'"{field}"' in worker, f"the worker should send {field}"
    for field in ("to_formats", "image_export_mode", "ocr", "table_structure"):
        assert f'"{field}"' in source, f"the service should accept {field}"


def test_the_parser_has_no_outbound_door_to_shut(compose):
    """The previous version of this test asserted five environment variables that switched off docling-
    serve's remote sources, vision-language APIs, plugins, browser UI and API docs.

    Those switches are gone because the surface is gone. The service is one endpoint that converts an
    uploaded document: it has no route that fetches a URL, no plugin loader, no UI and no published schema
    to disable one variable at a time. A smaller surface was the point of the wrapper, and this test
    records that the reason the old assertions existed no longer applies rather than deleting them
    quietly.

    What still needs checking is that nothing new opened a door.
    """
    env = compose["services"]["parser"]["environment"]
    source = (COMPOSE.parents[3] / "services" / "parser" / "parser_service.py").read_text()

    # No route that takes a URL, and no import of the machinery that would fetch one.
    for forbidden in ("HttpSource", "requests.get", "urlopen(", "allow_external_plugins",
                      "enable_remote_services"):
        assert forbidden not in source, f"the parser service should not use {forbidden}"
    # The API docs and schema endpoints FastAPI offers by default are switched off at construction.
    assert "docs_url=None" in source and "openapi_url=None" in source, (
        "FastAPI publishes /docs and /openapi.json unless told not to")
    # And a conversion runs one at a time, because the models are the memory.
    assert env.get("PARSER_MAX_CONCURRENT") == "1", (
        "a second concurrent conversion is a second copy of the models on a box sized for one")


def test_the_parser_healthcheck_can_actually_run(compose):
    """The image installs tesseract and GL libraries and no HTTP client, so a curl healthcheck fails
    with "not found" and the container is never healthy. This is a test rather than a comment because
    it is invisible until someone waits ten minutes for a service that was up the whole time."""
    test = compose["services"]["parser"]["healthcheck"]["test"]
    assert test[0] == "CMD"
    assert "curl" not in " ".join(test), "the parser image has no curl"
    assert "python" in test[1]


def test_the_parser_does_not_point_its_artifacts_path_at_an_empty_directory(compose):
    """The failure that stopped the parser's first start, 2026-10-11.

    DOCLING_SERVE_ARTIFACTS_PATH does not mean "cache the weights here". It means "load the weights
    from here and never download them", so setting it to an empty bind mount tells the service its
    weights are missing while forbidding it from fetching them:

        artifacts_path is set to a valid directory. No model weights will be downloaded at runtime.
        FileNotFoundError: RapidOCR artifacts not found or incomplete in artifacts_path.

    The image carries the weights it needs, so the correct configuration is to leave the variable
    unset. An air-gapped build is better served by weights inside the image than by a cache directory.
    """
    service = compose["services"]["parser"]
    assert "DOCLING_SERVE_ARTIFACTS_PATH" not in service.get("environment", {}), (
        "leave the artifacts path unset: it is a read location, not a cache, and pointing it at an "
        "empty directory stops the service from starting"
    )



def test_the_embedding_and_reranker_models_share_one_cache(compose):
    """Both download from Hugging Face on first start. One cache means a rebuild does not re-download
    them, and an office build can ship with the cache already filled."""
    services = compose["services"]
    embeddings = services["embeddings"]["volumes"]
    reranker = services["reranker"]["volumes"]
    assert embeddings == reranker, "the two services should share one model cache"
    assert any("hf-cache" in v for v in embeddings), embeddings
    for name in ("embeddings", "reranker"):
        assert services[name]["environment"]["HF_HOME"] == "/data"


def test_the_reranker_admits_a_full_candidate_pool(services):
    """The defect that stopped the first measurement, 2026-10-11.

    TEI consumes one infer permit per candidate and does not queue: a request that cannot get its
    permits is refused with 429 in tens of milliseconds. The reranker shipped with
    `--max-concurrent-requests=8`, which is exactly what one eight-candidate call needs, so three
    concurrent calls left two of them refused. Nine candidates in one call was refused by the same
    arithmetic. A pool of N candidates needs N permits, so the value has to cover the pool the
    benchmark sends rather than the number of callers anyone expected.
    """
    command = services["reranker"]["command"]
    permits = next((int(c.split("=", 1)[1]) for c in command
                    if isinstance(c, str) and c.startswith("--max-concurrent-requests=")), None)
    assert permits is not None, "the reranker must set --max-concurrent-requests explicitly"
    assert permits >= 32, (
        f"the reranker admits {permits} permits, which is below one full client batch of 32 "
        f"candidates; a request needing more permits than this is refused with 429 rather than queued"
    )


def test_the_cpu_reranker_is_a_bench_instrument_not_a_serving_component(services):
    """The decision of 2026-10-11, measured in step 3.3.

    The CPU reranker costs roughly 2.5 to 3 ms per token of candidate text, so a five-second budget
    buys three or four of our 500-token chunks. It is therefore out of the serving path: the cloud
    build reranks at Bedrock, where the pool of 40 to 50 is affordable because the silicon is not ours.
    The service stays in the file behind the `bench` profile, because it is the instrument that
    produced the measurement, on an instance with no GPU. A profile is what keeps it from starting by
    accident, the same way `office` keeps the local lane out of the cloud build.
    """
    assert services["reranker"]["profiles"] == ["bench"], (
        "the CPU reranker must sit behind the bench profile; the renderer sets COMPOSE_PROFILES from "
        "the manifest, so this leaves it stopped in every build"
    )
    assert "profiles" not in services["embeddings"], (
        "embeddings is the dense retriever and belongs in the default build; only the reranker moved"
    )




def test_the_ingestion_worker_mounts_documents_read_only(services):
    """This worker reads a client's documents and never writes to them. A read-only mount is the cheapest
    way to keep that true: a bug that tried to rewrite a source file would fail at the filesystem rather
    than silently editing what the client gave us."""
    mounts = services["ingestion"].get("volumes") or []
    documents = [str(mount) for mount in mounts if "/documents" in str(mount)]
    assert documents, "the ingestion worker has no documents mount"
    for mount in documents:
        assert mount.endswith(":ro"), f"the documents mount must be read-only: {mount}"


def test_the_ingestion_healthcheck_reads_the_cycle_not_the_process(services):
    """A worker with no listening socket cannot be asked whether it is well. A healthcheck that only
    proves the process exists stays green while every document fails, which is the failure mode worth
    catching, so the test is that the check consults the recorded outcome."""
    check = services["ingestion"]["healthcheck"]["test"]
    joined = " ".join(str(part) for part in check)
    assert "--status" in joined, f"the healthcheck must read the last cycle: {check}"
    assert "/state/last-cycle.json" in joined


def test_the_ingestion_worker_waits_for_what_it_calls(services):
    """Every call it makes is to a service, and starting before them turns the first cycle into a pile of
    connection errors that look like a code fault."""
    depends = services["ingestion"].get("depends_on") or []
    for required in ("postgres", "parser", "embeddings", "vector"):
        assert required in depends, f"ingestion should wait for {required}"


def test_the_ingestion_state_volume_is_declared(services, compose):
    """A named volume used but not declared makes the compose file invalid, and it is the kind of error
    that only appears on the box. Caught here instead."""
    volumes = compose.get("volumes") or {}
    assert "ingestion-state" in volumes, "the ingestion state volume is used but not declared"
    # And it must not be nested under networks, which is where it landed the first time.
    assert "ingestion-state" not in (compose.get("networks") or {}), (
        "the volume declaration ended up inside the networks block")
