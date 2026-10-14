r"""The access model: what a deployment declares, and what it resolves to.

`SEC-ACL-002`'s verify line is about retrieval against a live stack, which is `tests/fixtures/access_probe.py`'s
job. What this file can prove without a store is the part that decides: a registry read from a manifest, a
subject resolved to a permit, and every refusal that has to happen before a search runs. A permission model
whose bugs are only visible in a retrieval result is one where the failure looks like a bad answer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "retrieval"))
sys.path.insert(0, str(ROOT / "services" / "session"))

import access  # noqa: E402
import session as session_module  # noqa: E402

DECLARED = {
    "registry_version": 1,
    "declared": True,
    "deployment_id": "test",
    "roles": {"viewer": []},
    "groups": {
        "staff": ["company"],
        "finance": ["company", "finance"],
        "executive": ["company", "executive"],
    },
    "users": [
        {"subject": "dana@client.example", "name": "Dana Reyes", "groups": ["staff"]},
        {"subject": "sam@client.example", "name": "", "groups": ["staff", "finance"]},
        {"subject": "ada@client.example", "name": "", "groups": ["executive"]},
    ],
    "roots": ["company", "finance", "executive"],
}


def declared() -> access.Registry:
    return access.Registry.from_document(DECLARED)


# ------------------------------------------------------------------ what a registry is


def test_no_registry_file_is_the_builtin_two_folder_roles(tmp_path):
    """Every manifest on the box declares none, so this is the path a live deployment takes today."""
    registry = access.load(str(tmp_path / "absent.json"))
    assert registry.declared is False
    assert registry.roots_for_role("staff") == frozenset({"company"})
    assert registry.roots_for_role("executive") == frozenset({"company", "executive"})


def test_a_registry_without_a_version_is_refused(tmp_path):
    """A hand-written file is not a rendered one, and the renderer is where a manifest becomes enforceable."""
    path = tmp_path / "access.json"
    path.write_text(json.dumps({"roles": {"staff": ["company"]}}))
    with pytest.raises(access.AccessError, match="registry_version"):
        access.load(str(path))


def test_a_malformed_registry_is_refused_rather_than_falling_back(tmp_path):
    """Falling back to the built-in roles would silently widen or narrow access at the worst moment."""
    path = tmp_path / "access.json"
    path.write_text("{not json")
    with pytest.raises(access.AccessError, match="not valid JSON"):
        access.load(str(path))


def test_an_unknown_role_is_refused_and_names_the_known_ones():
    registry = declared()
    with pytest.raises(access.AccessError, match="unknown role"):
        registry.roots_for_role("admin")
    try:
        registry.roots_for_role("admin")
    except access.AccessError as exc:
        assert "viewer" in str(exc), "a refusal that does not say what would have worked sends the reader to the source"


def test_an_unknown_group_is_refused_rather_than_skipped():
    """Skipping turns a typo in a manifest into a user who silently sees less, which never reaches an operator."""
    registry = declared()
    with pytest.raises(access.AccessError, match="unknown group"):
        registry.roots_for_groups(["staff", "finace"])


def test_a_user_with_a_dangling_group_is_refused_at_load_time():
    document = dict(DECLARED)
    document["users"] = [{"subject": "x@client.example", "groups": ["nonexistent"]}]
    with pytest.raises(access.AccessError, match="does not declare"):
        access.Registry.from_document(document)


def test_a_user_with_no_groups_is_refused_at_load_time():
    document = dict(DECLARED)
    document["users"] = [{"subject": "x@client.example", "groups": []}]
    with pytest.raises(access.AccessError, match="no groups"):
        access.Registry.from_document(document)


def test_a_duplicate_subject_is_refused():
    document = dict(DECLARED)
    document["users"] = [
        {"subject": "x@client.example", "groups": ["staff"]},
        {"subject": "x@client.example", "groups": ["executive"]},
    ]
    with pytest.raises(access.AccessError, match="twice"):
        access.Registry.from_document(document)


# ------------------------------------------------------------------ what a permit resolves to


def test_access_is_the_union_of_the_subject_groups():
    registry = declared()
    assert access.resolve(registry, "dana@client.example", "viewer").roots == frozenset({"company"})
    assert access.resolve(registry, "sam@client.example", "viewer").roots == frozenset({"company", "finance"})
    assert access.resolve(registry, "ada@client.example", "viewer").roots == frozenset({"company", "executive"})


def test_the_union_has_no_precedence_table():
    """One user in two groups sees both. A model with no precedence is one that can be tested to exhaustion."""
    registry = declared()
    permit = access.resolve(registry, "sam@client.example", "viewer")
    assert permit.groups == ("staff", "finance")
    assert permit.roots == frozenset({"company", "finance"})


def test_an_undeclared_subject_is_refused_once_the_deployment_declares_users():
    registry = declared()
    with pytest.raises(access.AccessError, match="unknown subject"):
        access.resolve(registry, "nobody@client.example", "viewer")


def test_a_builtin_deployment_accepts_a_subject_it_has_no_list_for():
    """A deployment that declares no users has no list to check against, and its sessions are dev tooling."""
    registry = access.Registry.builtin()
    permit = access.resolve(registry, "dev@localhost", "staff")
    assert permit.roots == frozenset({"company"})
    assert permit.groups == ()


def test_the_permit_record_is_stable_under_ordering():
    """Two permits that mean the same thing have to hash the same, or the audit row is not reproducible."""
    registry = declared()
    a = access.resolve(registry, "sam@client.example", "viewer").as_record()
    b = access.resolve(registry, "sam@client.example", "viewer").as_record()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ------------------------------------------------------------------ the refusals a document boundary needs


def test_a_subject_outside_a_group_resolves_without_that_group_roots():
    """The requirement, stated as the property the probe then tests against the live store."""
    registry = declared()
    finance_only = registry.roots_for_groups(["finance"])
    staff = access.resolve(registry, "dana@client.example", "viewer").roots
    assert "finance" in finance_only
    assert "finance" not in staff
    assert finance_only - staff == {"finance"}, "the difference is the boundary the probe asks across"


# ------------------------------------------------------------------ the session, without a database


def test_mint_refuses_without_dev_and_says_what_replaces_it():
    registry = declared()
    store = session_module.MemoryStore()
    with pytest.raises(session_module.SessionError, match="--dev"):
        session_module.mint(registry, store, "dana@client.example", "viewer")
    assert store.rows == {}, "the guard must run before anything is written"


def test_mint_refuses_a_subject_the_deployment_does_not_declare():
    store = session_module.MemoryStore()
    with pytest.raises(access.AccessError, match="unknown subject"):
        session_module.mint(declared(), store, "nobody@client.example", "viewer", dev=True)
    assert store.rows == {}, "a refused identity must not leave a session behind"


def test_mint_refuses_an_unknown_role():
    store = session_module.MemoryStore()
    with pytest.raises(access.AccessError, match="unknown role"):
        session_module.mint(declared(), store, "dana@client.example", "admin", dev=True)
    assert store.rows == {}


def test_mint_refuses_a_ttl_beyond_the_ceiling():
    store = session_module.MemoryStore()
    with pytest.raises(session_module.SessionError, match="at most"):
        session_module.mint(declared(), store, "dana@client.example", "viewer", ttl_hours=48, dev=True)
    assert store.rows == {}


def test_a_minted_session_snapshots_its_roots():
    """A later change to a group must not change what an event recorded last week meant."""
    store = session_module.MemoryStore()
    session = session_module.mint(declared(), store, "dana@client.example", "viewer", dev=True)
    assert session.roots == frozenset({"company"})
    assert session.groups == ("staff",)
    assert store.get(session.session_id) is not None


def test_an_expired_session_does_not_resolve():
    import dataclasses
    from datetime import datetime, timedelta, timezone

    store = session_module.MemoryStore()
    session = session_module.mint(declared(), store, "dana@client.example", "viewer", dev=True)
    expired = dataclasses.replace(session, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    store.put(expired)
    with pytest.raises(session_module.SessionError, match="no live session"):
        session_module.resolve_token(store, session.session_id)


def test_a_revoked_session_does_not_resolve():
    store = session_module.MemoryStore()
    session = session_module.mint(declared(), store, "dana@client.example", "viewer", dev=True)
    assert store.revoke(session.session_id) is True
    with pytest.raises(session_module.SessionError, match="no live session"):
        session_module.resolve_token(store, session.session_id)


def test_an_empty_token_is_refused():
    with pytest.raises(session_module.SessionError, match="no session token"):
        session_module.resolve_token(session_module.MemoryStore(), "   ")


# ------------------------------------------------------------------ the refusal path


def test_a_role_claim_is_recorded_before_the_error_is_raised():
    """Order, not just outcome: the record has to exist by the time a caller learns it was refused."""
    store = session_module.MemoryStore()
    session = session_module.mint(declared(), store, "dana@client.example", "viewer", dev=True)
    written: list[dict] = []

    def recorder(_session, payload, *, dev=False):
        written.append(payload)

    with pytest.raises(session_module.SessionError, match="refused"):
        session_module.check_role_claim(session, "role", "executive", record=recorder)
    assert len(written) == 1, "the refusal must be recorded exactly once, before the raise"
    payload = written[0]
    assert payload["supplied_field"] == "role"
    assert payload["attempted_role"] == "executive"
    assert payload["session_role"] == "viewer"
    assert payload["roots_held"] == ["company"]


def test_a_claim_that_agrees_with_the_session_is_still_refused():
    """Accepting the ones that happen to match teaches a caller that the field is honoured."""
    store = session_module.MemoryStore()
    session = session_module.mint(declared(), store, "dana@client.example", "viewer", dev=True)
    with pytest.raises(session_module.SessionError, match="refused"):
        session_module.check_role_claim(session, "role", "viewer", record=lambda *a, **k: None)


def test_a_request_supplying_nothing_is_left_alone():
    """The complementary case. A check with no wrong path beside it is not a test."""
    store = session_module.MemoryStore()
    session = session_module.mint(declared(), store, "dana@client.example", "viewer", dev=True)
    session_module.check_role_claim(session, None, None, record=lambda *a, **k: None)


def test_the_recorded_actor_role_is_the_claimed_one():
    """The column a reviewer searches by holds the claim, and the real role goes in the payload."""
    store = session_module.MemoryStore()
    session = session_module.mint(declared(), store, "dana@client.example", "viewer", dev=True)
    captured: dict = {}

    def recorder(_session, payload, *, dev=False):
        captured.update(payload)

    with pytest.raises(session_module.SessionError, match="refused"):
        session_module.check_role_claim(session, "role", "executive", record=recorder)
    assert captured["attempted_role"] == "executive"
    assert captured["session_role"] == "viewer"


def test_a_refusal_that_cannot_be_recorded_fails_loudly(monkeypatch):
    """A refusal nobody can find afterwards is not a control, so a lost record is an error rather than a warning."""
    store = session_module.MemoryStore()
    session = session_module.mint(declared(), store, "dana@client.example", "viewer", dev=True)
    monkeypatch.setenv("AUDIT_TOOL", "/nonexistent/audit.py")
    session_module.AUDIT_TOOL = "/nonexistent/audit.py"
    with pytest.raises(session_module.SessionError, match="could not be recorded"):
        session_module.check_role_claim(session, "role", "executive", dev=True)


# ------------------------------------------------------------------ the renderer, end to end


def undeclared_manifest(tmp_path):
    r"""A manifest that declares no permission model, which is what every manifest used to be.

    Built from the example rather than from `dev-cloud.yaml`, because the development manifest declares the
    model the live probe needs and a test that used it as a stand-in for "nothing declared" would pass for
    the wrong reason the moment that changed. It did.
    """
    data = yaml.safe_load((ROOT / "manifests" / "example-standard-cloud.yaml").read_text())
    data["access"].pop("roots", None)
    data["access"].pop("groups", None)
    data["access"].pop("users", None)
    data["access"].pop("roles", None)
    path = tmp_path / "undeclared.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_the_renderer_writes_the_builtin_registry_when_the_manifest_declares_none(tmp_path):
    import subprocess

    out = tmp_path / "rendered"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render_manifest.py"),
         "--manifest", str(undeclared_manifest(tmp_path)), "--out", str(out)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads((out / "access.json").read_text())
    assert document["declared"] is False
    assert document["users"] == []
    assert set(document["roles"]) == {"staff", "executive"}


def test_the_renderer_refuses_a_manifest_that_declares_groups_and_no_roots(tmp_path):
    import subprocess

    manifest = yaml.safe_load(undeclared_manifest(tmp_path).read_text())
    manifest["access"].update({"groups": {"staff": {"roots": ["company"]}}, "users": []})
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(manifest))
    out = tmp_path / "rendered"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render_manifest.py"), "--manifest", str(path),
         "--out", str(out)],
        capture_output=True, text=True)
    assert result.returncode == 1
    assert "access.roots is missing" in result.stdout


def test_a_declared_manifest_renders_the_registry_the_runtime_reads(tmp_path):
    import subprocess

    manifest = yaml.safe_load(undeclared_manifest(tmp_path).read_text())
    # The registry keys are added to the manifest's own access block rather than replacing it, because the
    # block also carries the egress declaration and dropping it makes the manifest invalid for a different
    # reason than the one under test.
    manifest["access"].update({
        "roots": ["company", "finance"],
        "groups": {"staff": {"roots": ["company"]}, "finance": {"roots": ["company", "finance"]}},
        "users": [{"subject": "dana@client.example", "name": "Dana", "groups": ["staff"]}],
    })
    path = tmp_path / "declared.yaml"
    path.write_text(yaml.safe_dump(manifest))
    out = tmp_path / "rendered"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render_manifest.py"), "--manifest", str(path),
         "--out", str(out)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    registry = access.load(str(out / "access.json"))
    assert registry.declared is True
    assert access.resolve(registry, "dana@client.example", "viewer").roots == frozenset({"company"})


def test_the_rendered_registry_never_carries_a_model_field():
    """A session resolves access and never a model role. A reader looking for one will not find it."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rendered"
        manifest = Path(tmp) / "undeclared.yaml"
        manifest.write_text(yaml.safe_dump(yaml.safe_load(
            (ROOT / "manifests" / "example-standard-cloud.yaml").read_text())))
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "render_manifest.py"),
             "--manifest", str(manifest), "--out", str(out)],
            capture_output=True, text=True, check=True)
        document = json.loads((out / "access.json").read_text())
        assert "model" not in json.dumps(document)
        assert set(document) == {"registry_version", "declared", "deployment_id", "roles", "groups",
                                 "users", "roots"}
