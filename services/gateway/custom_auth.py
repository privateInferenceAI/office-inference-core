"""The gateway's identity hook: a signed statement becomes a role, and the role becomes a team.

Loaded by LiteLLM through `general_settings.custom_auth`. Everything it enforces is a decision this
repository made, which is why it is code here rather than configuration somewhere:

* **A role comes from a signed statement, never from a plain header.** A header carrying a role is a header a
  caller can edit, and that is the same failure as a role in a request body. The signature is over a payload
  containing the role, so editing it invalidates it.
* **A statement is spent once.** Without the nonce, a captured assertion is replayable for its whole life.
* **The master key is recognised and exempted.** With `custom_auth` enabled the gateway sends the master key
  through this hook. A hook that only understands role assertions therefore refuses it, and every management
  endpoint becomes unreachable. Found by running it; the documentation does not mention it.

**What this hook deliberately does not do.** It does not decide budgets, model allowlists or spend. It resolves
an identity and hands it to the gateway, because those checks are the gateway's own and a second implementation
here would be a second answer to the same question. The team it names is what makes a budget enforceable, and
the spike confirmed the enforcement is real: over the ceiling the gateway refused with `budget_exceeded`.
"""
from __future__ import annotations

import os

from fastapi import Request

from litellm.proxy._types import UserAPIKeyAuth

import assertion

# Where the hook finds the key and the nonce file. The same paths the API uses, mounted into both, which is
# what makes them agree without either holding the other's copy.
# The roles this deployment exposes as model names. Read from the environment the renderer writes,
# which is the same source the gateway took its model list from.
_KNOWN_ROLES = frozenset(
    name.strip() for name in (os.environ.get("MODEL_ROLES") or "").split(",") if name.strip()
) | frozenset({"front_desk", "analyst", "reader", "coder"})

KEY_PATH = os.environ.get("OI_ASSERTION_KEY", "/app/rendered/assertion.key")


def _master_key() -> str:
    """Read at call time rather than import time, which is a bug the session tool had first."""
    return os.environ.get("LITELLM_MASTER_KEY", "")


async def _requested_model(request) -> str:
    """The model named in the request body, or an empty string when it cannot be read."""
    import json

    # `request.body()` rather than a private attribute: Starlette caches the read, so this costs
    # nothing later when the gateway parses the same body for the model call.
    try:
        body = await request.body()
    except Exception:  # noqa: BLE001
        return ""
    if not body:
        return ""
    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        return str(json.loads(body).get("model") or "")
    except Exception:  # noqa: BLE001 - an unreadable body is not this check's business
        return ""


async def _refuse_a_model_this_role_may_not_use(request, role: str) -> None:
    """Refuse a request whose model is another role's, which is what a forged statement would try."""
    asked = await _requested_model(request)
    if not asked or asked == role:
        return
    # A deployment may name a variant of the role, such as `analyst-frontier` on a build with both lanes.
    # A DIFFERENT role's name is the thing being refused, and it is a prefix comparison rather than an
    # allowlist because the gateway takes models from the request rather than from a table it holds.
    if asked == role + "-frontier":
        return
    if asked in _KNOWN_ROLES:
        raise Exception(
            f"this statement was minted for {role!r} and the request asks for {asked!r}. A role may "
            f"only spend its own statement, and the model permission is what stops one role reaching "
            f"another role's model.")

# **Prisma's pool parameters are not libpq's, and LiteLLM's process adds them to this environment.**
# LiteLLM rewrites `DATABASE_URL` in-process at startup with the parameters Prisma's pool understands
# (`connection_limit`, `pool_timeout`, `socket_timeout`, `max_idle_connection_lifetime`, `pgbouncer`) so the
# reader and writer pools agree. psycopg 3 rejects any URI query parameter it does not recognise, so a
# connection built from that string fails with `invalid URI query parameter: "connection_limit"` — which the
# gateway surfaces as an authentication error, reading like a bad key rather than a bad DSN. Every query a
# client sends went through one of these connections, so on 2026-10-16 the deployment could not answer a
# question at all and the symptom looked like a broken virtual key. Plain parameters psycopg does know, such
# as `sslmode` and `connect_timeout`, are kept.
_PRISMA_ONLY_PARAMS = frozenset({
    "connection_limit",
    "pool_timeout",
    "socket_timeout",
    "max_idle_connection_lifetime",
    "pgbouncer",
})


def _connection_url(required: bool = True) -> str | None:
    """The deployment's DSN with Prisma's pool parameters removed, or None when there is none."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        if required:
            raise Exception("no DATABASE_URL, so no ceiling could be read and no call may be allowed")
        return None
    parts = urlsplit(url)
    kept = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in _PRISMA_ONLY_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def _team_rate_limits(role: str):
    """The team's tokens and requests per minute, or None when it declares neither.

    **Why this exists.** The team row always had these columns and the auth hook never read them, so a
    rate limit declared on a team was silently unenforced. A limit that is not passed to the component
    that enforces it is not a limit; it is a comment.
    """
    import psycopg

    url = _connection_url(required=False)
    if not url:
        return (None, None)
    conn = psycopg.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select tpm_limit, rpm_limit from \"LiteLLM_TeamTable\" where team_id = %s", (role,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return (None, None)
    return (int(row[0]) if row[0] else None, int(row[1]) if row[1] else None)


def _team_budget(role: str) -> float:
    """How much of this role's ceiling is left, which is what the gateway enforces.

    Read per request rather than cached, because the number changes as the gateway records spend and a stale
    ceiling is a ceiling that lets a runaway through. One indexed row read on a local socket is cheaper than
    the model call it guards.
    """
    import psycopg

    conn = psycopg.connect(_connection_url())
    try:
        with conn.cursor() as cur:
            cur.execute(
                # tpm_limit and rpm_limit are read here because they were NOT before: the table had
                # them, the identity object accepts them, and a ceiling nobody passed to the gateway
                # was a ceiling nobody enforced. Found while trying to provoke a real throttle for 4.4.
                'select max_budget, spend, tpm_limit, rpm_limit from "LiteLLM_TeamTable" '
                'where team_id = %s', (role,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise Exception(
            f"no team named {role!r} exists, so this role has no ceiling and no call may be allowed. "
            f"Run: oi --profile tools run --rm keys --provision-teams"
        )
    ceiling, spend = row[0], row[1]  # the query also returns tpm_limit and rpm_limit, read by _team_rate_limits
    if ceiling is None:
        # A deployment that declared no ceiling for this role. The manifest allows it, and the honest answer
        # is a number the gateway can enforce a spend against rather than an unlimited lane.
        return float(os.environ.get("OI_UNBUDGETED_ROLE_CEILING", "1.0"))
    remaining = float(ceiling) - float(spend or 0)
    if remaining <= 0:
        # Zero rather than a refusal here, so the gateway produces its own budget error and the API reports
        # a refusal with the numbers in it, which is a better message than anything this hook would invent.
        return 0.0
    return remaining

async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    """Resolve the caller, or refuse with the reason."""
    # The exemption. Without it, enabling this hook locks the deployment out of its own administration.
    master = _master_key()
    if master and api_key and api_key == master:
        return UserAPIKeyAuth(api_key=api_key, user_role="proxy_admin")

    token = request.headers.get(assertion.HEADER, "")
    if not token:
        raise Exception(
            f"no {assertion.HEADER} header. This deployment authenticates a model call with a signed "
            f"assertion minted by the assistant API; there is no long-lived key to present instead."
        )

    try:
        key = assertion.load_key(KEY_PATH)
        payload = assertion.verify_model_call(key, token)
        assertion.spend_nonce(payload)
    except assertion.AssertionError_ as exc:
        # The message is passed through to the caller on purpose. These refusals are about the shape of the
        # request rather than about what the client may read, so naming the check that failed helps the
        # operator and tells a caller nothing they did not already send.
        raise Exception(f"the assertion was refused: {exc}") from exc

    role = payload["role"]

    # ------------------------------------------------------------------ the model permission
    #
    # **A statement may only be spent on the role it names.** Without this, any signed statement reached
    # any model: the assertion names a role, the role picks the team, and the REQUESTBODY names the
    # model, with nothing joining the two. The drill the manual describes, asking as one role and naming
    # another, returned 200 instead of a refusal, which is how this was found.
    #
    # It matters because the roles are priced differently and read different things: `reader` is the
    # expensive one, and the gateway exposes each role as a model name. A statement for `analyst` asking
    # for `reader` is a caller reaching past what it was granted.
    await _refuse_a_model_this_role_may_not_use(request, role)

    # ------------------------------------------------------------------ the budget
    #
    # **Returning `team_id` is not enough, and this cost a day to learn.** The gateway does not read the team
    # row's ceiling for an identity that came from this hook: set a team's spend to 99 against a ceiling of 10
    # and a request still answers. What the spike enforced was the `max_budget` ON THE IDENTITY IT RETURNED,
    # which made the spike look like proof that team budgets work when it was proof of something else.
    #
    # So the remainder is computed here and returned as the identity's budget. The gateway then refuses once
    # that remainder is spent, and it accumulates the spend against the team row as usual, which is what keeps
    # the figure current across restarts: this reads the same row the gateway writes.
    #
    # **A ceiling that cannot be read is a refusal, not an allowance.** A database that is unreachable must
    # not turn a budget into an unmetered lane, which is the direction this fails in and the reason it is a
    # raise rather than a default.
    budget = _team_budget(role)
    limits = _team_rate_limits(role)

    return UserAPIKeyAuth(
        max_budget=budget,
        # Passed through so the gateway enforces them. None is passed through as None, which is
        # how a deployment says it has no rate limit rather than one of zero.
        tpm_limit=limits[0],
        rpm_limit=limits[1],
        api_key="oi-assertion",
        # The team is what carries the ceiling. One team per role, provisioned from the manifest.
        team_id=role,
        user_id=payload.get("session_id") or "oi-session",
        metadata={"role": role, "session_id": payload.get("session_id", ""), "source": "oi-assertion"},
    )