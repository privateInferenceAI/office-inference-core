"""What a deployment's intake configuration must satisfy, checked before anything is fetched.

Every rule in this file exists because getting it wrong is invisible from the running system. A shared
mailbox authenticates badly rather than not at all. An application permission reads one mailbox perfectly and
every other one too. An alert recipient on the vendor's domain works exactly as well as one on the client's,
right up to the day the vendor is gone. None of those fails loudly, which is why they are refused here rather
than documented somewhere.

The refusal messages name the finding that produced them, because a client's administrator reads them. The
research behind each one is in `docs/research/email-intake/handover-owned-email-intake.md`.

**Ownership is configuration rather than code**, which is the only way `cord-check` can be a command that
runs on a live deployment. The five components `CON-OWNED-001` names are declared in the client's own file
with an owner beside each, and the check refuses any owner that is not the client.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import net

# What we control. A domain matching any of these in a client-facing field is the defect this module exists
# to catch, and the substring check is deliberately looser than a suffix match: `officeinference-support.com`
# and `intake.officeinference.net` are both ours, and a list of exact domains would miss the second one.
VENDOR_MARKERS = ("officeinference", "office-inference")

# Office Inference's own Entra tenant ids, so the check can refuse a deployment whose identity tenant is ours.
#
# **Empty, and that is a finding rather than a placeholder.** Every other rule in this file compares the
# client's declarations against each other, which catches the realistic mistake (an app registration in a
# different tenant from the mailbox) and cannot catch a configuration that consistently names our tenant for
# everything. Turning that into an absolute check needs the ids written down here, and this repository does
# not hold them: they live in the administration repository, which is local and sensitive.
#
# `test_a_deployment_in_our_own_tenant_is_refused` sets this list and asserts the refusal, so the rule is
# tested and the only thing missing is the value. Recording the two tenant ids here is the whole fix, and it
# belongs to whoever owns the business records rather than to this service.
VENDOR_TENANT_IDS: tuple[str, ...] = ()

# Exactly two scopes are allowed, and the list is an allowlist rather than a denylist. A denylist of known
# bad permissions is a list that stops covering the problem the first time Microsoft adds a permission.
ALLOWED_SCOPES = ("Mail.Read", "offline_access")

REFUSED_SCOPES = {
    "Mail.ReadWrite": "writing to the mailbox is not needed to take a document in, and it is the permission "
                      "that lets intake delete the evidence of what it read",
    "Mail.Send": "intake never sends mail. Sending needs its own permission and its own decision",
    "Mail.ReadBasic": "it reads metadata without the body, so attachments cannot be taken. It looks like a "
                      "narrower choice and it is an unusable one",
    "Mail.Read.Shared": "it reaches mailboxes the client did not name",
    "User.Read.All": "the directory is not needed to read one mailbox",
    "Directory.Read.All": "the directory is not needed to read one mailbox",
}

CLIENT = "client"


@dataclass
class Problem:
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass
class Config:
    raw: dict
    path: str
    problems: list[Problem] = field(default_factory=list)

    # -- accessors, so callers do not sprinkle .get() chains and default differently in two places ---------

    @property
    def client_name(self) -> str:
        return self.raw.get("client", {}).get("name", "unnamed")

    @property
    def deployment_id(self) -> str:
        return self.raw.get("deployment_id", "unknown")

    @property
    def tenant_id(self) -> str:
        return self.raw.get("identity", {}).get("tenant_id", "")

    @property
    def client_domains(self) -> list[str]:
        return list(self.raw.get("client", {}).get("domains") or [])

    @property
    def mailbox(self) -> dict:
        return self.raw.get("mailbox", {})

    @property
    def intake(self) -> dict:
        return self.raw.get("intake", {})

    @property
    def documents_root(self) -> Path:
        return Path(self.intake.get("documents_root", "/srv/oi-core/documents"))

    @property
    def state_dir(self) -> Path:
        return Path(self.intake.get("state_dir", "/var/lib/oi-intake"))

    @property
    def poll_seconds(self) -> int:
        return int(self.intake.get("poll_seconds", 120))

    @property
    def secret_path(self) -> str:
        return (self.raw.get("secret_store", {}).get("path") or "")

    @property
    def alerts(self) -> dict:
        return self.raw.get("alerts", {})

    @property
    def allowlist(self) -> list[str]:
        return [entry.lower().lstrip("@") for entry in (self.intake.get("allowlist") or [])]

    @property
    def scopes(self) -> list[str]:
        return list((self.raw.get("identity", {}).get("permission", {}).get("scopes") or []))

    def graph(self, key: str, default: str) -> str:
        return self.raw.get("graph", {}).get(key, default)

    def owned_by_client(self) -> bool:
        return not self.problems

    def as_record(self) -> dict:
        """The part of the configuration the record may hold. No paths into a secret, no token."""
        return {
            "deployment_id": self.deployment_id,
            "client": self.client_name,
            "mailbox": self.mailbox.get("address"),
            "folder": self.mailbox.get("folder"),
            "tenant_id": self.tenant_id,
            "permission": self.raw.get("identity", {}).get("permission", {}).get("type"),
            "scopes": self.scopes,
        }


def is_vendor(name: str) -> bool:
    lowered = (name or "").lower()
    return any(marker in lowered for marker in VENDOR_MARKERS)


def domain_of(address: str) -> str:
    return (address or "").rsplit("@", 1)[-1].lower().strip(".")


def domain_is_client(domain: str, client_domains: list[str]) -> bool:
    domain = (domain or "").lower()
    return any(domain == owned or domain.endswith("." + owned) for owned in client_domains)


def load(path: str | Path) -> Config:
    """Read the file and validate it. Problems are returned rather than raised, so one run shows all of them."""
    target = Path(path)
    if not target.exists():
        return Config(raw={}, path=str(target),
                      problems=[Problem("no-config", f"no configuration at {target}")])
    try:
        raw = yaml.safe_load(target.read_text()) or {}
    except yaml.YAMLError as exc:
        return Config(raw={}, path=str(target),
                      problems=[Problem("bad-yaml", f"{target} is not valid YAML: {exc}")])
    if not isinstance(raw, dict):
        return Config(raw={}, path=str(target),
                      problems=[Problem("bad-yaml", f"{target} holds {type(raw).__name__}, not a mapping")])
    config = Config(raw=raw, path=str(target))
    config.problems = validate(config)
    return config


def validate(config: Config) -> list[Problem]:
    """Every rule, in the order a person setting this up would meet them."""
    problems: list[Problem] = []
    raw = config.raw

    # -- the client, and the domains that decide ownership ------------------------------------------------

    domains = config.client_domains
    if not domains:
        problems.append(Problem("no-domains",
                                "client.domains is empty, so no address can be shown to be the client's"))
    for domain in domains:
        if is_vendor(domain):
            problems.append(Problem(
                "vendor-domain",
                f"client.domains names {domain}, which is a domain Office Inference controls. "
                "BIZ-INDEPENDENT-001: cutting every cord has to leave the deployment working, and a "
                "client domain that is ours is the cord."))

    # -- the mailbox ---------------------------------------------------------------------------------------

    mailbox = config.mailbox
    address = mailbox.get("address") or ""
    if not address:
        problems.append(Problem("no-address", "mailbox.address is empty"))
    elif not domain_is_client(domain_of(address), domains):
        problems.append(Problem(
            "address-not-client",
            f"mailbox.address is {address}, which is not on a domain in client.domains. The intake address "
            "is the client's, unchanged from whatever they already use."))

    kind = (mailbox.get("kind") or "").lower()
    if kind != "regular_user":
        problems.append(Problem(
            "shared-mailbox" if kind == "shared" else "mailbox-kind",
            "mailbox.kind must be regular_user. A shared mailbox cannot sign in at all: Microsoft keeps "
            "sign-in blocked on the associated account and its password is system-generated, and delegated "
            "OAuth needs one interactive sign-in to mint the refresh token. Intake on a shared mailbox is "
            "therefore forced onto application permission, which reads every mailbox in the tenant."))

    if not mailbox.get("licence"):
        problems.append(Problem(
            "no-licence",
            "mailbox.licence is empty. Graph reads a licensed mailbox, and a mailbox whose licence has been "
            "removed returns HTTP 403 while the credential keeps working."))

    # -- the registration, the consent, and the one permission --------------------------------------------

    identity = raw.get("identity") or {}
    tenant = identity.get("tenant_id") or ""
    app = identity.get("app") or {}
    consent = identity.get("consent") or {}

    if not tenant:
        problems.append(Problem("no-tenant", "identity.tenant_id is empty"))
    elif tenant in VENDOR_TENANT_IDS:
        problems.append(Problem(
            "tenant-is-ours",
            f"identity.tenant_id is {tenant}, which is an Office Inference tenant. The app registration is "
            "created in the client's tenant because it cannot be moved afterwards, and a deployment whose "
            "identity lives in ours is the cord BIZ-INDEPENDENT-001 exists to cut."))
    if app.get("tenant_id") and app["tenant_id"] != tenant:
        problems.append(Problem(
            "app-elsewhere",
            f"identity.app.tenant_id is {app['tenant_id']} and the deployment's tenant is {tenant}. An app "
            "registration cannot be moved between tenants, so one created in our tenant cannot be handed "
            "over later; it has to be created in the client's from the start."))
    if app.get("registered_by") and app["registered_by"] != CLIENT:
        problems.append(Problem(
            "app-not-clients",
            f"identity.app.registered_by is {app['registered_by']!r} rather than {CLIENT!r}. The client must "
            "be able to rotate the secret and change the redirect URI without asking us."))
    if consent.get("tenant_id") and consent["tenant_id"] != tenant:
        problems.append(Problem(
            "consent-elsewhere",
            f"identity.consent.tenant_id is {consent['tenant_id']} rather than the deployment's tenant"))
    if consent.get("granted_by") and consent["granted_by"] != CLIENT:
        problems.append(Problem(
            "consent-not-clients",
            f"identity.consent.granted_by is {consent['granted_by']!r} rather than {CLIENT!r}"))

    permission = identity.get("permission") or {}
    kind = (permission.get("type") or "").lower()
    if kind != "delegated":
        problems.append(Problem(
            "application-permission" if kind == "application" else "permission-type",
            "identity.permission.type must be delegated. Application Mail.Read reaches every mailbox in the "
            "tenant, and the Exchange scope that limits it is additive to the Entra grant rather than a "
            "replacement for it, so an administrator who completes three of four steps builds something that "
            "looks scoped and is not. Delegated Mail.Read reads the signed-in user's mail and needs no "
            "PowerShell."))
    for scope in config.scopes:
        if scope in REFUSED_SCOPES:
            problems.append(Problem("scope-refused", f"identity.permission.scopes includes {scope}: "
                                                     f"{REFUSED_SCOPES[scope]}"))
        elif scope not in ALLOWED_SCOPES:
            problems.append(Problem(
                "scope-unknown",
                f"identity.permission.scopes includes {scope}, which is not one of the two this deployment "
                f"uses ({', '.join(ALLOWED_SCOPES)}). A permission list that grows quietly is the "
                "prompt-and-consent failure mode."))
    if "Mail.Read" not in config.scopes:
        problems.append(Problem("no-mail-scope", "identity.permission.scopes does not include Mail.Read, so "
                                                 "no mail can be read"))

    # -- the secret store ----------------------------------------------------------------------------------

    store = raw.get("secret_store") or {}
    if store.get("owner") != CLIENT:
        problems.append(Problem(
            "secret-not-clients",
            f"secret_store.owner is {store.get('owner')!r} rather than {CLIENT!r}. The credential is the "
            "client's, in the client's store; a copy on our side is a copy they cannot revoke."))
    secret_path = store.get("path") or ""
    if not secret_path:
        problems.append(Problem("no-secret-path", "secret_store.path is empty"))
    elif not secret_path.startswith("/"):
        problems.append(Problem("secret-relative",
                                f"secret_store.path is {secret_path}, which is relative and would resolve "
                                "against whatever directory the service happened to start in"))

    # -- the two hosts, and the cord -----------------------------------------------------------------------

    graph = raw.get("graph") or {}
    for key in ("login_host", "api_host"):
        host = (graph.get(key) or "").strip()
        if not host:
            problems.append(Problem("no-host", f"graph.{key} is empty"))
        elif host not in net.ALLOWED_HOSTS:
            problems.append(Problem(
                "host-not-microsoft",
                f"graph.{key} is {host!r} and intake may reach only {', '.join(net.ALLOWED_HOSTS)}. A host "
                "we control here is the cord BIZ-INDEPENDENT-001 exists to cut."))
        elif is_vendor(host):
            problems.append(Problem(
                "vendor-host", f"graph.{key} is {host!r}, which is infrastructure Office Inference operates"))

    # -- the alerts ----------------------------------------------------------------------------------------

    alerts = config.alerts or {}
    recipients = alerts.get("recipients") or []
    if not recipients:
        problems.append(Problem(
            "no-alert-recipient",
            "alerts.recipients is empty. The silent failures are caught by a heartbeat and a volume floor, "
            "and an alert with nowhere to go catches nothing."))
    for recipient in recipients:
        domain = domain_of(recipient)
        if is_vendor(domain) or not domain_is_client(domain, domains):
            problems.append(Problem(
                "alert-not-clients",
                f"alerts.recipients includes {recipient}. Alerts route to an address the client controls: "
                "an alert to us reintroduces exactly the dependency the design removes, and it does it "
                "quietly, because nothing about a working alert looks wrong."))

    # -- the intake paths ----------------------------------------------------------------------------------

    for key in ("documents_root", "state_dir"):
        value = (raw.get("intake") or {}).get(key) or ""
        if not value:
            problems.append(Problem("no-path", f"intake.{key} is empty"))
        elif not value.startswith("/"):
            problems.append(Problem("relative-path",
                                    f"intake.{key} is {value} relative, which would move with the working "
                                    "directory"))

    root = (raw.get("intake") or {}).get("root")
    if not root:
        problems.append(Problem(
            "no-root",
            "intake.root is empty. It is the top-level folder the invoice is deposited under, and the "
            "ingestion worker takes the access label from it, so a deposit with no root lands unlabelled."))

    if int((raw.get("intake") or {}).get("poll_seconds", 120)) <= 0:
        problems.append(Problem("poll-interval", "intake.poll_seconds must be greater than zero"))

    return problems


def ownership(config: Config) -> list[dict]:
    """The five components, with the owner each declares.

    This is what `cord-check` prints. It reads the same fields `validate` refuses on, so a table that prints
    `client` five times and a validation that refuses a vendor value cannot disagree.
    """
    identity = config.raw.get("identity") or {}
    app = identity.get("app") or {}
    consent = identity.get("consent") or {}
    store = config.raw.get("secret_store") or {}
    return [
        {"component": "intake address", "value": config.mailbox.get("address"),
         "owner": CLIENT if domain_is_client(domain_of(config.mailbox.get("address", "")),
                                             config.client_domains) else "unknown"},
        {"component": "mailbox", "value": f"{config.mailbox.get('address')} "
                                          f"({config.mailbox.get('kind')}, {config.mailbox.get('licence')})",
         "owner": CLIENT if (config.mailbox.get("kind") == "regular_user"
                             and config.mailbox.get("licence")) else "unknown"},
        {"component": "app registration", "value": f"{app.get('display_name', 'unnamed')} "
                                                    f"in tenant {app.get('tenant_id', 'unset')}",
         "owner": app.get("registered_by") or "unknown"},
        {"component": "consent grant", "value": f"granted by {consent.get('granted_by', 'unset')} "
                                                f"in tenant {consent.get('tenant_id', 'unset')}",
         "owner": consent.get("granted_by") or "unknown"},
        {"component": "credential", "value": store.get("path", "unset"),
         "owner": store.get("owner") or "unknown"},
    ]
