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
KEY_PATH = os.environ.get("OI_ASSERTION_KEY", "/app/rendered/assertion.key")


def _master_key() -> str:
    """Read at call time rather than import time, which is a bug the session tool had first."""
    return os.environ.get("LITELLM_MASTER_KEY", "")


def _team_budget(role: str) -> float:
    """How much of this role's ceiling is left, which is what the gateway enforces.

    Read per request rather than cached, because the number changes as the gateway records spend and a stale
    ceiling is a ceiling that lets a runaway through. One indexed row read on a local socket is cheaper than
    the model call it guards.
    """
    import psycopg

    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise Exception("no DATABASE_URL, so no ceiling could be read and no call may be allowed")
    conn = psycopg.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select max_budget, spend from \"LiteLLM_TeamTable\" where team_id = %s", (role,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise Exception(
            f"no team named {role!r} exists, so this role has no ceiling and no call may be allowed. "
            f"Run: oi --profile tools run --rm keys --provision-teams"
        )
    ceiling, spend = row
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

    return UserAPIKeyAuth(
        max_budget=budget,
        api_key="oi-assertion",
        # The team is what carries the ceiling. One team per role, provisioned from the manifest.
        team_id=role,
        user_id=payload.get("session_id") or "oi-session",
        metadata={"role": role, "session_id": payload.get("session_id", ""), "source": "oi-assertion"},
    )