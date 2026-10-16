"""What the credential can reach, enumerated rather than assumed.

`CON-LEAST-001` and `SEC-INTAKE-002` both ask for the same thing, and the register says why the obvious test
is the wrong one: "the test enumerates what the credential can reach rather than asserting the permission it
was granted, because the two differ when a scope is additive."

That sentence is the whole design. Exchange's RBAC for Applications is **additive to the Entra grant** rather
than a replacement for it, and Microsoft's own worked example of getting it wrong ends with an assignment
that allows access to everything. An administrator who completes three of four steps has built something that
looks scoped and is not. Asserting on the declared permission would pass on that deployment. So this module
makes the requests and reports what came back.

Six probes, and exactly one of them is expected to succeed:

| Probe | Expected | What it proves |
|---|---|---|
| the mailbox this deployment reads | reached | intake works at all |
| a second mailbox in the same tenant | refused | the credential is one mailbox, not the tenant |
| the directory | refused | no `User.Read.All` was quietly left behind |
| the signed-in user's profile | refused | the credential is `Mail.Read` and nothing else |
| a different Microsoft service | refused | the grant does not travel to SharePoint or OneDrive |
| a host we control | refused before the socket | `BIZ-INDEPENDENT-001` holds at the network layer |

The last row is the one worth keeping. It is checked by `net.check_url` rather than by the tenant, so it
holds whether or not Microsoft would have refused, and it is the control the cord check leans on.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import net

GRAPH = "https://graph.microsoft.com/v1.0"

REACHED = "reached"
REFUSED = "refused"
UNEXPECTED = "unexpected"


@dataclass
class Probe:
    name: str
    target: str
    expected: str
    observed: str
    detail: str = ""
    status: int | None = None

    @property
    def ok(self) -> bool:
        return self.observed == self.expected

    def as_record(self) -> dict:
        return asdict(self) | {"ok": self.ok}


def _probe(name: str, target: str, url: str, token: "object", expected: str = REFUSED) -> Probe:
    headers = {"Authorization": f"Bearer {token.access_token}"}
    try:
        response = net.request(url, headers=headers, timeout=30)
    except net.HostRefused as exc:
        # Refused before the socket. This is the allowlist acting, and the detail says so rather than
        # reporting it as a tenant refusal, because the two are different controls.
        return Probe(name, target, expected, REFUSED, f"refused by the host allowlist: {exc}", None)
    except net.HttpError as exc:
        return Probe(name, target, expected, REFUSED, f"HTTP {exc.status}: {exc.body[:200]}", exc.status)
    except Exception as exc:  # noqa: BLE001 - a probe that cannot run is reported, never silently skipped
        return Probe(name, target, expected, UNEXPECTED, f"{type(exc).__name__}: {exc}", None)
    return Probe(name, target, expected, REACHED, f"HTTP {response.status}", response.status)


def enumerate_reach(config, token) -> dict:
    """Run every probe and report what happened. `ok` is false when any probe did not do what it must."""
    folder = config.mailbox.get("folder") or "Inbox"
    second = (config.raw.get("reach") or {}).get("second_mailbox")

    probes = [
        _probe("own_mailbox", f"the folder {folder} in the mailbox this deployment reads",
               f"{GRAPH}/me/mailFolders/{folder}/messages?$top=1", token, REACHED),
        _probe("second_mailbox", "another mailbox in the same tenant",
               f"{GRAPH}/users/{second}/messages?$top=1" if second
               else f"{GRAPH}/users/unknown@invalid.example/messages?$top=1", token),
        _probe("directory", "the tenant's user directory", f"{GRAPH}/users?$top=1", token),
        _probe("profile", "the signed-in user's profile", f"{GRAPH}/me", token),
        _probe("other_service", "a different Microsoft service", f"{GRAPH}/sites?$top=1", token),
        _probe("vendor_host", "a host Office Inference controls",
               "https://intake.officeinference.com/v1.0/me/messages", token),
    ]

    granted = list(getattr(token, "scopes", []) or [])
    scope_problems = []
    for scope in granted:
        short = scope.split("/")[-1]
        if short in ("Mail.ReadWrite", "Mail.Send", "Mail.Read.Shared", "User.Read.All", "Directory.Read.All"):
            scope_problems.append(f"{short} is granted and is not needed to read one mailbox")
        if short == "Mail.Read":
            continue
        if short not in ("offline_access", "openid", "profile"):
            scope_problems.append(f"{short} is granted and is not one of the two scopes this deployment uses")

    return {
        "ok": all(probe.ok for probe in probes) and not scope_problems,
        "probes": [probe.as_record() for probe in probes],
        "granted_scopes": granted,
        "scope_problems": scope_problems,
        "reached": [probe.name for probe in probes if probe.observed == REACHED],
    }


def format_reach(report: dict) -> str:
    width = max(len(probe["name"]) for probe in report["probes"]) if report["probes"] else 0
    lines = []
    for probe in report["probes"]:
        mark = "ok  " if probe["ok"] else "FAIL"
        lines.append(f"{mark} {probe['name']:<{width}}  {probe['observed']:<10} "
                     f"(expected {probe['expected']})  {probe['target']}")
        if probe["detail"]:
            lines.append(f"       {probe['detail'][:160]}")
    for problem in report["scope_problems"]:
        lines.append(f"FAIL scope  {problem}")
    lines.append(f"{len(report['reached'])} of {len(report['probes'])} probes reached anything, "
                 f"and exactly one should have: {', '.join(report['reached']) or 'none'}")
    return "\n".join(lines)
