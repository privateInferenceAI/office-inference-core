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
    posture is that nothing of ours is reachable from outside the machine."""
    services = compose["services"]
    for name in ("vector", "embeddings", "reranker"):
        service = services[name]
        assert "sha256:" in service["image"], f"{name} must be digest-pinned"
        assert "ai-net" in service["networks"], f"{name} must be on the private network"
        assert not service.get("ports"), f"{name} must not publish a port"


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


