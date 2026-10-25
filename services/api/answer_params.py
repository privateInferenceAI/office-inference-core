"""Request parameters that differ by model provider.

**A field one provider accepts can be a hard error on another.** Every measured answer path here runs at
temperature zero, and the app used to send that field unconditionally. Bedrock Kimi K3 rejects the
temperature field outright, so swapping the analyst model failed before the first token. The field is a
deployment setting now: unset keeps the deterministic default, an empty value omits the key, and a number
sets it. Existing providers see the same payload they saw before; a provider that rejects the field can be
served without a second request builder.
"""
from __future__ import annotations

import os


def temperature_param(environ=None) -> dict:
    """The temperature key for a model request, or nothing when the model takes none.

    **Unset and empty mean different things.** Unset is a deployment that has not thought about it and gets
    the measured default of zero. Empty is a deployment saying this model rejects the field. A number is a
    deliberate sampling choice, which no measured path here uses but the setting allows.
    """
    env = os.environ if environ is None else environ
    raw = env.get("OI_TEMPERATURE")
    if raw is None:
        return {"temperature": 0}
    raw = raw.strip()
    if not raw:
        return {}
    try:
        return {"temperature": float(raw)}
    except ValueError:
        # A malformed value falls back to the deterministic default rather than failing the request. The
        # deployment's rendered env is the place to catch it, not a user's question.
        return {"temperature": 0}
