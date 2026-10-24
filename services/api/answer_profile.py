"""The answer path's one setting.

**Why this exists.** The path accumulated ten environment switches: how many passages to seed, how long a
snippet is, how many evidence slots exist, the search limit, and switches for the loop, the row query, the
exact-phrase search, the honesty gate, and the step and call caps. Every one was a deployment fact that had
to be set, documented and tested in combination, and a wrong combination was a silent quality regression.

The product has two answer profiles and nothing else. The values are measurements, not preferences: the
accuracy profile is the configuration that scored 37 of 37 on the second corpus in five consecutive runs,
and the economy profile is the older smaller-evidence configuration for a deployment that trades recall for
context and latency. A deployment names one profile; the code chooses the rest.

**The tool-assisted loop is part of the profile now.** It used to be a client flag on the stream and an
unconditional behaviour on the JSON path, which meant a client had to know which transport got the measured
answer. The accuracy profile ran the loop and the economy profile did not, so the answer quality is a
deployment fact rather than something the interface chooses. An explicit `tools` value in a request still
overrides the profile, which is what the probes and the integration tests use.
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
    tools: bool


PROFILES = {
    "accuracy": AnswerProfile(name="accuracy", seed_passages=12, snippet_chars=1600,
                              max_evidence=32, search_limit=12, tools=True),
    "economy": AnswerProfile(name="economy", seed_passages=6, snippet_chars=700,
                             max_evidence=24, search_limit=8, tools=False),
}

DEFAULT = "accuracy"


def current() -> AnswerProfile:
    name = (os.environ.get("OI_ANSWER_PROFILE") or DEFAULT).strip().lower()
    return PROFILES.get(name, PROFILES[DEFAULT])


def tools_enabled(requested=None) -> bool:
    """Whether the tool-assisted loop runs for this request.

    The profile decides. An explicit request value still wins, because the probes and the integration tests
    need to run either path on a deployment whose profile would choose the other one. `None` versus `False`
    is the whole distinction: absent means the deployment decides, and an explicit false is a client asking
    for the single-shot path.
    """
    if requested is None:
        return current().tools
    return bool(requested)
