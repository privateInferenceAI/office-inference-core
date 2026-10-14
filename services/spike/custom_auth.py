"""A spike: can LiteLLM's custom_auth hook carry our role, enforce a budget, and NOT lock out admin?

Questions, answered by being loaded:

  1. Does custom_auth load in the pinned version, and what must the returned object carry?
  2. Does a team budget enforce when the identity comes from custom_auth rather than a minted key?
  3. Does team_id have to be a real row?
  4. Does the hook receive the api_key, so it can recognise the master key and let admin through?

Finding so far, recorded here because it is the interesting part: a custom_auth that never returns admin
locks the deployment out of its own management endpoints. Editing that answer while working on the spike is
worth nothing; putting it to the register is worth something.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import Request

from litellm.proxy._types import UserAPIKeyAuth

SECRET = os.environ.get("OI_ASSERTION_SECRET", "spike-secret").encode()
MASTER = os.environ.get("LITELLM_MASTER_KEY", "")


def _sign(role: str) -> str:
    return hmac.new(SECRET, role.encode(), hashlib.sha256).hexdigest()


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    """Resolve the caller, and allow the master key so administration is not locked out."""
    # Question 4. If the hook sees the master key, it can return admin rather than refusing, which is the
    # difference between a usable hook and one that bricks the deployment.
    print(f"[spike] hook called: api_key={api_key[:14]!r} matches_master={api_key == MASTER}", flush=True)
    if MASTER and api_key == MASTER:
        print("[spike] master key recognised, returning admin", flush=True)
        return UserAPIKeyAuth(api_key=api_key, user_role="proxy_admin")

    role = request.headers.get("x-oi-role", "")
    assertion = request.headers.get("x-oi-assertion", "")
    if not role:
        raise Exception("no x-oi-role header: the spike needs one to resolve an identity")
    if not assertion or not hmac.compare_digest(assertion, _sign(role)):
        raise Exception("signature does not match: refused")

    return UserAPIKeyAuth(
        api_key="spike",
        team_id=role,
        user_id="spike-user",
        metadata={"role": role, "source": "spike"},
    )


def sign(role: str) -> str:
    return _sign(role)