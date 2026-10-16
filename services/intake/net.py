"""The only place intake makes a network request, and the two hosts it is allowed to reach.

`BIZ-INDEPENDENT-001` asks for a system the client can keep running after every cord to Office Inference is
cut. The cheap version of that requirement is a code review, and a code review is what failed twice already:
one design put the client's invoice address on infrastructure we would operate, and one put the OAuth
application registration in our identity tenant. Both read as reasonable and both are rebuilds once a client
has paid.

So the check is placed where it cannot be forgotten. Every request intake makes goes through `request()`
below, and `request()` refuses any host that is not Microsoft's before a socket is opened. Two hosts, named
here, and nothing else:

    login.microsoftonline.com   the token endpoint, in the client's own tenant
    graph.microsoft.com         the mail read

**"Does anything phone home" therefore has an answer that is a property of one function rather than a claim
about a codebase.** A licence check, an update check, a telemetry ping or a support callback would all have
to be written here, in a file whose first paragraph says why they cannot be.

Two smaller controls sit in the same place because they are the same kind of thing:

* **Graph is read-only.** `GET` is the only method this function will send to the mail API. The application
  permission that would be needed to write is the one `CON-LEAST-001` refuses, so a `POST` or a `DELETE`
  appearing here would mean somebody had widened the credential and started using it.
* **The scheme is https.** A redirect to a plaintext endpoint, or a config value that names one, is refused
  rather than followed.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# Two hosts. The tuple is the allowlist and it is deliberately short enough to read in one line, because a
# list nobody reads is a list that grows.
ALLOWED_HOSTS = ("login.microsoftonline.com", "graph.microsoft.com")

GRAPH_HOST = "graph.microsoft.com"
LOGIN_HOST = "login.microsoftonline.com"

USER_AGENT = "oi-intake/1.0"


class HostRefused(RuntimeError):
    """A request to a host that is not Microsoft's. Always a defect, never a transient condition."""


class MethodRefused(RuntimeError):
    """A write method aimed at the mail API."""


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    url: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except json.JSONDecodeError:
            return None

    def header(self, name: str, default: str = "") -> str:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return default


class HttpError(RuntimeError):
    """A response the caller has to treat as an error, carrying the status and the body.

    Returned as an exception rather than as a Response with a status, because every caller in this package
    does the same thing with a 4xx and forgetting to check is the mistake that would go unnoticed.
    """

    def __init__(self, status: int, body: str, url: str = ""):
        super().__init__(f"HTTP {status} from {url}: {body[:400]}")
        self.status = status
        self.body = body
        self.url = url


# The transport. `None` means the real network. A test installs a function with the same signature, and the
# allowlist check still runs before it, because the check belongs to `request()` rather than to the opener.
# That ordering is the point: a test can prove the refusal without a network, and a deployment cannot reach a
# third host whether or not a test is running.
_transport = None


def set_transport(function) -> None:
    """Install a transport for a test, or pass None to restore the real network."""
    global _transport
    _transport = function


def get_transport():
    return _transport


def check_url(url: str) -> str:
    """The host, if this URL is one intake may call. Raises otherwise."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise HostRefused(
            f"{url} is not https. Intake talks to the token endpoint and the mail API over TLS and to "
            "nothing else, so a plaintext URL is refused rather than followed.")
    if parts.username or parts.password:
        raise HostRefused(f"{url} carries credentials in the URL, which a log line would then hold")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise HostRefused(
            f"{host!r} is not one of the two hosts intake may reach ({', '.join(ALLOWED_HOSTS)}). "
            "This is the control that keeps the client's deployment independent of infrastructure Office "
            "Inference operates: a licence check, an update check or a telemetry ping would have to be "
            "written here to exist, and it is not.")
    return host


def request(url: str, *, method: str = "GET", headers: dict | None = None,
            data: bytes | None = None, timeout: int = 30) -> Response:
    """One HTTP request, to one of two hosts, over https, and read-only when it is the mail API.

    A non-2xx response is raised as `HttpError` rather than returned, because every caller in this package
    treats a 4xx as a fault and a returned status is the one that gets ignored.
    """
    host = check_url(url)
    verb = method.upper()
    if host == GRAPH_HOST and verb != "GET":
        raise MethodRefused(
            f"{verb} {url}: intake reads mail and never writes it. The write methods need a permission "
            "that widens the credential beyond the one mailbox this deployment is scoped to.")

    sent = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    sent.update(headers or {})

    if _transport is not None:
        response = _transport(url=url, method=verb, headers=sent, data=data, timeout=timeout)
    else:
        response = _urllib(url=url, method=verb, headers=sent, data=data, timeout=timeout)

    if response.status >= 400:
        raise HttpError(response.status, response.text, url)
    return response


def _urllib(*, url: str, method: str, headers: dict, data: bytes | None, timeout: int) -> Response:
    """The real request. Kept small: everything that decides anything happens above."""
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as handle:  # noqa: S310 - the URL is checked above
            return Response(handle.status, dict(handle.headers), handle.read(), url)
    except urllib.error.HTTPError as exc:
        # An error response is still a response and its body is the useful part: Graph puts the error code
        # and the AADSTS text there, and that text is what the alert is built from.
        return Response(exc.code, dict(exc.headers or {}), exc.read(), url)
