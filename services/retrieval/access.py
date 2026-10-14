"""Who may read which documents, and the refusal when the answer is nobody.

`SEC-ACL-002` asks for per-user and per-group permissions beyond the two folder roles. **This module is the
data model; the enforcement stays in `retriever.py`, inside the search.** The split is deliberate. A
permission model that computes an answer and leaves the filter to a caller is one where every caller is a
chance to forget, and the requirement's own verify line — *a user outside a document's group cannot retrieve
it by any query* — is a claim about the query, not about a helper function.

**Three levels, each one earning its place:**

* A **root** is an access boundary and the value stored in a chunk's `acl` label. It renames what
  `SEC-ACL-001` already filters on rather than adding a mechanism beside it.
* A **group** is a named union of roots. It is how a person's access actually changes: a job changes, a
  group changes, and no code moves.
* A **user** is a subject with the groups it belongs to. It is what the identity provider authenticates and
  what a session is minted for.

**Access is a union and there are no deny rules.** One user in two groups sees the union of both, with no
precedence table and no exceptions. A model with no precedence is one that can be tested to exhaustion, and
the union is a single line a reader can check by eye.

**Two properties this module holds, and both are refusals rather than defaults:**

* An **unknown role or group raises**. Defaulting would grant the broader of two permissions to a typo, which
  is the worst available failure.
* A **role or group with no roots raises**. A permission set that is empty is a configuration error, and
  treating it as "a user who happens to see nothing" makes a mistake look like a quiet deployment.

**The registry is rendered from the manifest and read once at startup.** It is materialized rather than
looked up per request for the reason the audit record gives: a later change to a group must not change what an
event recorded last week meant. A session snapshots the roots it resolved, and this module is what resolved
them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Where the renderer writes the registry. Overridable because the probe and the tests run from the checkout,
# where there is no /app, and because the API container mounts the deployment's rendered directory rather
# than baking it into an image.
DEFAULT_REGISTRY_PATH = os.environ.get("ACCESS_REGISTRY", "/app/rendered/access.json")

# The built-in default, which is what every manifest on the box declares today: two folder roles, and no
# users. A deployment that declares nothing keeps working unchanged, so adding the registry to the schema
# changes no existing behaviour. **Absent is not the same as empty**: an explicit `access.groups: {}` is a
# manifest that declares a permission model with nothing in it, which is refused rather than read as "use the
# default".
DEFAULT_ROLES: dict[str, frozenset[str]] = {
    "staff": frozenset({"company"}),
    "executive": frozenset({"company", "executive"}),
}

# Which roots exist when a manifest declares none. Derived from the default roles rather than written twice,
# so the two cannot drift.
DEFAULT_ROOTS: frozenset[str] = frozenset().union(*DEFAULT_ROLES.values())


class AccessError(ValueError):
    """A registry that cannot be trusted, refused rather than interpreted.

    Everything in this module that raises raises this, so a caller can tell a configuration refusal from a
    bug without reading messages. The API turns it into a 500 with the message intact, because a deployment
    that cannot answer "who is this" must not fall back to answering everyone.
    """


@dataclass(frozen=True)
class User:
    """One subject, with the groups it belongs to."""

    subject: str
    groups: tuple[str, ...] = ()
    name: str = ""

    def display(self) -> str:
        return self.name or self.subject


@dataclass(frozen=True)
class Permit:
    """An access decision, complete enough to hand to the retriever and to write into a record.

    Frozen because it is the authority for one request: a permit that can be edited after it is resolved is a
    permission check that happens at a moment nobody can name. The role is carried beside the roots so the
    audit row can say which role was *held* without re-deriving it from the registry, which may have moved
    since.
    """

    subject: str
    role: str
    roots: frozenset[str]
    groups: tuple[str, ...] = ()
    session_id: str = ""

    def as_record(self) -> dict:
        """The fields an audit row carries. Sorted, so two permits that mean the same thing hash the same."""
        return {
            "role": self.role,
            "roots": sorted(self.roots),
            "subject": self.subject,
            "groups": sorted(self.groups),
        }


@dataclass
class Registry:
    """The access model a deployment declared, and the only thing that may resolve a permit.

    Loaded once and passed by reference. Nothing here is a global, so a test can build one, and a deployment
    can hold two without them arguing.
    """

    roles: dict[str, frozenset[str]] = field(default_factory=lambda: dict(DEFAULT_ROLES))
    groups: dict[str, frozenset[str]] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)
    declared: bool = False

    # ------------------------------------------------------------------ resolution

    def roots_for_role(self, role: str) -> frozenset[str]:
        """What a role may read, or a refusal naming what the deployment does know.

        The message lists the known roles because the common cause is a typo or a stale session, and a
        refusal that does not say what would have worked sends the operator to the source.

        **An empty role is legal here, and it is the declared-registry case rather than an oversight.** A
        deployment that models access with groups declares a role that carries no roots of its own, so every
        person's permission comes from their groups and there is exactly one place access is written down. A
        role that does not exist at all is still refused: an unknown role is a typo or a stale session, and
        defaulting it would grant the broader of two permissions.
        """
        if role not in self.roles:
            raise AccessError(
                f"unknown role {role!r}. Known roles: {sorted(self.roles)}. A permission set is refused "
                f"rather than defaulted, because defaulting grants the broader of two permissions to a typo.")
        return self.roles[role]

    def roots_for_groups(self, groups) -> frozenset[str]:
        """The union of several groups, refusing any the deployment does not declare.

        Refusing an unknown group rather than skipping it is the safe direction and the honest one: skipping
        turns a typo in a manifest into a user who silently sees less, which is the failure that never
        reaches an operator.
        """
        unknown = sorted(set(groups) - set(self.groups))
        if unknown:
            raise AccessError(
                f"unknown group(s) {unknown}. Known groups: {sorted(self.groups)} or none declared. A group "
                f"is refused rather than skipped, because skipping turns a typo into a quiet loss of access.")
        roots: frozenset[str] = frozenset()
        for group in groups:
            roots |= self.groups[group]
        if not roots:
            raise AccessError(
                f"groups {sorted(groups)} are permitted no document roots, which is a configuration error "
                f"rather than a user with no documents")
        return roots

    def user(self, subject: str) -> User:
        """One subject, or a refusal. A subject the deployment does not declare is not a user."""
        if subject not in self.users:
            raise AccessError(
                f"unknown subject {subject!r}. This deployment declares {len(self.users)} user(s): "
                f"{sorted(self.users) or '(none)'}. A subject is refused rather than defaulted, because a "
                f"default here invents an identity.")
        return self.users[subject]

    def permit_for_session(self, subject: str, role: str, *, session_id: str = "") -> Permit:
        """The access a session holds: the role's roots, plus the roots of the subject's groups.

        **The role comes from the session and the groups come from the manifest**, and the permission is the
        union of the two. In the built-in model the role is the whole decision, because no users are declared
        and there are no groups to add, which is why this behaves exactly as it did before the registry
        existed.

        In a declared model the role typically carries nothing and the groups carry everything, so the union
        is simply the groups. Both cases run through this one function on purpose: a second resolution path
        for declared deployments is a second place for the permission to be decided, and the requirement is
        that access is filtered by the store rather than assembled by whoever remembered.

        **A subject the deployment does not declare is refused**, unless the deployment declares no users at
        all — a built-in deployment has no user list, and its sessions are development tooling rather than
        authenticated people. Once users exist, an unknown subject is an identity that was never granted, and
        inventing one is the failure the refusal prevents.
        """
        roots = self.roots_for_role(role)
        groups: tuple[str, ...] = ()
        if self.users:
            user = self.user(subject)
            groups = user.groups
            roots = roots | self.roots_for_groups(groups)
        if not roots:
            raise AccessError(
                f"subject {subject!r} resolves to no document roots. The role {role!r} permits none and its "
                f"groups {list(groups)} permit none either, which is a configuration error rather than a user "
                f"with no documents.")
        return Permit(subject=subject, role=role, roots=roots, groups=groups, session_id=session_id)

    # ------------------------------------------------------------------ construction

    @classmethod
    def builtin(cls) -> "Registry":
        """The default model, for a deployment that declares none."""
        return cls()

    @classmethod
    def from_document(cls, document: dict) -> "Registry":
        """Build from the rendered JSON, refusing anything malformed.

        Every refusal names the field it came from, because this runs at startup and the person reading the
        message is looking at a manifest rather than at this file.
        """
        if not isinstance(document, dict):
            raise AccessError(f"the access registry must be an object, got {type(document).__name__}")

        roles_raw = document.get("roles") or {}
        groups_raw = document.get("groups") or {}
        users_raw = document.get("users") or []

        if not isinstance(roles_raw, dict) or not roles_raw:
            raise AccessError("the access registry declares no roles, so no session could resolve anything")

        roles = {}
        for name, roots in roles_raw.items():
            # A role may legitimately permit nothing: a deployment that writes its access at the group level
            # declares a role that carries no roots of its own. A GROUP may not, because that is the level
            # access is actually granted from, and an empty one is a configuration error.
            roles[name] = _roots(roots, f"roles.{name}", allow_empty=True)

        groups = {}
        for name, roots in groups_raw.items():
            groups[name] = _roots(roots, f"groups.{name}")

        users: dict[str, User] = {}
        for index, entry in enumerate(users_raw):
            if not isinstance(entry, dict):
                raise AccessError(f"users[{index}] must be an object, got {type(entry).__name__}")
            subject = (entry.get("subject") or "").strip()
            if not subject:
                raise AccessError(f"users[{index}] has no subject, so nothing could authenticate as it")
            if subject in users:
                raise AccessError(f"subject {subject!r} is declared twice, and one of the two would be dead")
            declared_groups = entry.get("groups") or []
            unknown = sorted(set(declared_groups) - set(groups))
            if unknown:
                raise AccessError(
                    f"users[{index}] ({subject}) names group(s) {unknown}, which this registry does not "
                    f"declare. Known groups: {sorted(groups)}. A dangling group membership is refused here "
                    f"rather than at a request, where it would look like a permission bug.")
            if not declared_groups:
                raise AccessError(
                    f"users[{index}] ({subject}) declares no groups, so it would resolve to no access at "
                    f"all. In a declared registry access comes from groups, so a user without one is a "
                    f"configuration error rather than a person who happens to see nothing.")
            users[subject] = User(subject=subject, groups=tuple(declared_groups),
                                  name=(entry.get("name") or "").strip())

        return cls(roles=roles, groups=groups, users=users, declared=True)

    # ------------------------------------------------------------------ reporting

    def roots(self) -> frozenset[str]:
        """Every root any role or group names, for the validator and for a diagnostic."""
        roots: frozenset[str] = frozenset()
        for value in list(self.roles.values()) + list(self.groups.values()):
            roots |= value
        return roots

    def describe(self) -> str:
        """One line per level, for the CLI and for the manual's worked example."""
        lines = [f"roles   {len(self.roles)}: " + ", ".join(f"{k}={sorted(v)}" for k, v in sorted(self.roles.items()))]
        if self.groups:
            lines.append(f"groups  {len(self.groups)}: " + ", ".join(f"{k}={sorted(v)}" for k, v in sorted(self.groups.items())))
        if self.users:
            lines.append(f"users   {len(self.users)}: " +
                         ", ".join(f"{u.subject}[{','.join(u.groups) or '-'}]" for _, u in sorted(self.users.items())))
        return "\n".join(lines)


def _roots(value, where: str, *, allow_empty: bool = False) -> frozenset[str]:
    r"""A root set from a manifest field, refused if it is empty or not a list of names.

    \`allow_empty\` is for roles only. A group with no roots grants nothing and is a configuration error; a
    role with no roots is the declared-registry case, where every person's permission comes from their groups.
    """
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise AccessError(f"{where} must be a list of root names, got {type(value).__name__}")
    roots = frozenset(str(item).strip() for item in value if str(item).strip())
    if not roots and not allow_empty:
        raise AccessError(
            f"{where} is empty, which is a configuration error rather than a group that sees nothing")
    return roots


def load(path: str | None = None) -> Registry:
    """The registry for this deployment, or the built-in default when none was rendered.

    **A missing file is not an error and a malformed one is.** Absent means the deployment declared no access
    block, which is every manifest on the box today, and the default is the documented behaviour. Present but
    broken means somebody meant something and it did not survive rendering, and falling back there would
    silently widen or narrow access at exactly the moment nobody is looking.
    """
    target = Path(path or DEFAULT_REGISTRY_PATH)
    if not target.exists():
        return Registry.builtin()
    try:
        document = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise AccessError(f"{target} is not valid JSON: {exc}. Re-run scripts/render_manifest.py.") from exc
    if document.get("registry_version") is None:
        raise AccessError(
            f"{target} has no registry_version, so it was not written by scripts/render_manifest.py. A "
            f"hand-written registry is refused, because the renderer is where a manifest becomes an "
            f"enforceable decision.")
    return Registry.from_document(document)


def resolve(registry: Registry, subject: str, role: str, *, session_id: str = "") -> Permit:
    """The one call a session or a test needs: a subject and a role become a permit."""
    return registry.permit_for_session(subject, role, session_id=session_id)
