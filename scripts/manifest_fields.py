"""Shared manifest model-field parsing for the renderer, validator and lifecycle check.

**One parser, because a fallback chain has one meaning.** The renderer emits it, the validator policies
it and the lifecycle check dates it. Three parsers would drift, and the deployment would end up with
whatever the most lenient of them allowed.

A fallback entry is `bedrock/<model-id>` with an optional `@<region>` suffix. The suffix means
"invoke this same model from that Region first", which is how Bedrock's own regional routing becomes
the first recovery step and a different model becomes the second. A plain string in the manifest is a
chain of one, so the single-fallback manifests written before this parser keep working unchanged.
"""
from __future__ import annotations

import re

# An AWS Region code, including the GovCloud form. Deliberately narrow: a fallback region is a
# deployment decision and a typo in it should fail in CI, not at the moment the primary is down.
REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z]+-\d$")

# The gateway is given the fallbacks in order and tries each once. Three is a bound, not a target:
# each entry is a provider call in the failure path, and a long chain hides which models a deployment
# may quietly depend on.
MAX_FALLBACKS = 3


def fallback_chain(entry) -> list[str]:
    """The declared fallback chain, in order. A single string is a chain of one."""
    value = (entry or {}).get("fallback")
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def split_fallback(value: str) -> tuple[str, str]:
    """(model id, region) from `bedrock/<id>` or `bedrock/<id>@<region>`."""
    model, _, region = str(value).partition("@")
    return model.strip(), region.strip()


def is_region(value: str) -> bool:
    return bool(REGION.match((value or "").strip()))
