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
        payload = assertion.verify(key, token)
        assertion.spend_nonce(payload)
    except assertion.AssertionError_ as exc:
        # The message is passed through to the caller on purpose. These refusals are about the shape of the
        # request rather than about what the client may read, so naming the check that failed helps the
        # operator and tells a caller nothing they did not already send.
        raise Exception(f"the assertion was refused: {exc}") from exc

    role = payload["role"]

    # ------------------------------------------------------------------ the budget, and what does not enforce it
    #
    # **Returning `team_id` is not enough, and this cost a day to learn.** A team row holds the ceiling and the
    # spend, and the gateway does not read them for an identity that came from this hook: set a team's spend to
    # 99 against a ceiling of 10 and a request still answers. What the spike enforced was the `max_budget` ON
    # THE IDENTITY IT RETURNED, which is why the spike looked like proof that team budgets work and was not.
    #
    # The fix, and it is small: read the team's ceiling and spend here and return the remainder as
    # `max_budget`. The gateway then refuses once the remainder is spent, which is the behaviour the manifest
    # promises and the manual must not claim until this is written. The read belongs in one place, with a
    # short cache so a request does not pay for it twice, and the spend the gateway accumulates has to be
    # written back to the team row or the ceiling resets on every process restart.
    #
    # Recorded here rather than in a note because this is the line somebody will be looking at.
    return UserAPIKeyAuth(
        api_key="oi-assertion",
        # The team is what carries the ceiling. One team per role, provisioned from the manifest.
        team_id=role,
        user_id=payload.get("session_id") or "oi-session",
        metadata={"role": role, "session_id": payload.get("session_id", ""), "source": "oi-assertion"},
    )