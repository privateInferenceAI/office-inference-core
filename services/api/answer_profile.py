"""The answer path's one setting.

**Why this exists.** The path accumulated ten environment switches: how many passages to seed, how long a
snippet is, how many evidence slots exist, the search limit, and switches for the loop, the row query, the
exact-phrase search, the honesty gate, and the step and call caps. Every one was a deployment fact that had
to be set, documented and tested in combination, and a wrong combination was a silent quality regression.

The product has two answer profiles and nothing else. The values are measurements, not preferences: the
accuracy profile is the configuration that scored 37 of 37 on the second corpus in five consecutive runs,
and the economy profile is the older smaller-evidence configuration for a deployment that trades recall for
context and latency. A deployment names one profile; the code chooses the rest.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AnswerProfile:
    name: str
    seed_passages: int
    snippet_chars: int
    max_evidence: int
    search_limit: int


PROFILES = {
    "accuracy": AnswerProfile(name="accuracy", seed_passages=12, snippet_chars=1600,
                              max_evidence=32, search_limit=12),
    "economy": AnswerProfile(name="economy", seed_passages=6, snippet_chars=700,
                             max_evidence=24, search_limit=8),
}

DEFAULT = "accuracy"


def current() -> AnswerProfile:
    name = (os.environ.get("OI_ANSWER_PROFILE") or DEFAULT).strip().lower()
    return PROFILES.get(name, PROFILES[DEFAULT])
