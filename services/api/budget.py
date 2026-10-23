"""Bounded time, safe retries, and an answer when the provider will not give one.

**One budget, three thresholds, and every wait drawn from it.** A per-attempt timeout is not a budget: three
attempts at ten seconds is thirty seconds of waiting, and the caller asked once. The deadline is wall-clock from
the moment the request arrived, and a retry happens only if what remains can hold it.

| threshold | value | where it comes from |
|---|---|---|
| first token | 1.5s | measured p50 260ms, p95 359ms on the frontier lane, four times the observed worst |
| stall | 5s | nothing measured; a gap this long is a failure rather than a slow model |
| total | 30s | provisional. The longest measured answer was 2.9s and the tail is unmeasured |

**A stream that never starts and a stream that starts then stalls are different failures**, with different
causes and different words in the record. That is why there are three thresholds and not one.

**Classification is on the error type and never on the status.** A Bedrock 429 covers both the quota throttle,
which is ours and worth waiting for, and ModelNotReady, which is not ours to wait out.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# Bumped whenever a rule below changes, and recorded on every degraded row. The chain is append-only, so a
# classification read six months from now needs to know which rule set produced it.
TAXONOMY_VERSION = 1

FIRST_TOKEN_SECONDS = float(os.environ.get('OI_FIRST_TOKEN_SECONDS', '1.5'))
STALL_SECONDS = float(os.environ.get('OI_STALL_SECONDS', '5'))
TOTAL_SECONDS = float(os.environ.get('OI_TOTAL_SECONDS', '30'))

# Kept back so the degraded answer can still be built, streamed and recorded inside the same budget. A
# degradation that itself runs past the deadline is not a degraded answer, it is a slower failure.
DELIVERY_RESERVE_SECONDS = float(os.environ.get('OI_DELIVERY_RESERVE_SECONDS', '1.2'))
MAX_ATTEMPTS = int(os.environ.get('OI_MAX_ATTEMPTS', '3'))

# **The local lane is a different shape, so it gets different numbers.** Everything above was measured
# against a remote frontier model: first token at p50 260ms, p95 359ms. A llama.cpp server on CPU is
# compute-bound on prompt processing, so its first token is dominated by the prompt rather than by the
# network, and inheriting the frontier figure would degrade every local answer as if it were a failure.
# These are placeholders until the office build is measured, and the runbook says so rather than implying
# they are derived. llama.cpp publishes `prompt_tokens_seconds` and `predicted_tokens_seconds`, which is
# what the derivation should use.
LOCAL_FIRST_TOKEN_SECONDS = float(os.environ.get("OI_LOCAL_FIRST_TOKEN_SECONDS", "20"))
LOCAL_STALL_SECONDS = float(os.environ.get("OI_LOCAL_STALL_SECONDS", "30"))
LOCAL_TOTAL_SECONDS = float(os.environ.get("OI_LOCAL_TOTAL_SECONDS", "180"))


# Waiting will not help for these. Matched case-insensitively against whatever the provider said, because the
# string arrives through a gateway and its exact form is not ours to promise.
# **Both the type token and the message a person would recognise.** The first version matched only the token,
# so their human-readable form, which is what actually arrives in the body, classified as unclassified and
# would have been retried. Found by a test rather than by a throttle, which is the cheaper way round.
TERMINAL_TYPES = (
    'budget_exceeded',
    'budget has been exceeded',
    'budget exceeded',
    'envelope_spend_limit',
    'enforced_spend_limit',
    'insufficient_quota',
    'invalid_api_key',
    'authentication_error',
    'permission_denied',
    'access_denied',
    'validation_exception',
    'invalid_request',
    'content_filter',
    'content_policy',
    'model_not_found',
    'not_found',
)

# The provider is busy rather than broken. Named rather than inferred, so an unknown error is treated as
# terminal: a retry spent on an unrecognised failure is a retry spent on a request that probably cannot
# succeed, and the budget it consumes is the budget the degraded answer needed.
RETRYABLE_TYPES = (
    'throttling',
    'throttling_exception',
    'too_many_requests',
    # The provider message, not the SDK type. Bedrock returns this exact sentence as the body of a 429;
    # matching only the underscore form classified a real throttle as unrecognised and skipped the retry.
    'too many requests',
    'rate_limit',
    'rate limit',
    'service_unavailable',
    'internal_server',
    'timeout',
    'connection',
    'overloaded',
)


@dataclass
class Budget:
    """One request's deadline, and where inside it the request currently is."""

    @classmethod
    def for_lane(cls, lane: str, started: float | None = None) -> "Budget":
        """The thresholds that match where the model actually runs.

        **Why this exists rather than one set of numbers.** The frontier figures were measured against
        Bedrock. A llama.cpp server on CPU is a different shape, and borrowing the frontier thresholds
        would mark every healthy local answer as a failure. Research on 2026-10-14 found the deployed
        code doing exactly that: module-level defaults, and a degraded payload that said frontier while
        a local model served.
        """
        if lane == "local":
            return cls(total=LOCAL_TOTAL_SECONDS, first_token=LOCAL_FIRST_TOKEN_SECONDS,
                       stall=LOCAL_STALL_SECONDS, started=started if started is not None else time.monotonic())
        return cls(started=started if started is not None else time.monotonic())

    total: float = TOTAL_SECONDS
    first_token: float = FIRST_TOKEN_SECONDS
    stall: float = STALL_SECONDS
    reserve: float = DELIVERY_RESERVE_SECONDS
    started: float = field(default_factory=time.monotonic)
    first_token_at: float | None = None
    last_chunk_at: float | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        return self.total - self.elapsed()

    def spent(self) -> bool:
        """Whether the deadline has passed."""
        return self.remaining() <= 0

    def past_reserve(self) -> bool:
        """Whether what is left is only enough to deliver a degraded answer."""
        return self.remaining() <= self.reserve

    def note_chunk(self) -> None:
        now = time.monotonic()
        if self.first_token_at is None:
            self.first_token_at = now
        self.last_chunk_at = now

    def timed_out(self) -> str | None:
        """Which threshold has tripped, or None.

        Most specific first: a request that never produced a token has not stalled, and a request that
        stalled may still be inside the total cap.
        """
        now = time.monotonic()
        if self.first_token_at is None:
            if now - self.started > self.first_token:
                return 'first_token'
        elif self.last_chunk_at is not None and now - self.last_chunk_at > self.stall:
            return 'stall'
        if self.remaining() <= 0:
            return 'total'
        return None

    def describe(self) -> dict:
        return {
            'elapsed_ms': int(self.elapsed() * 1000),
            'remaining_ms': int(self.remaining() * 1000),
            'first_token_ms': int((self.first_token_at - self.started) * 1000) if self.first_token_at else None,
            'first_token_seconds': self.first_token,
            'stall_seconds': self.stall,
            'total_seconds': self.total,
            'reserve_seconds': self.reserve,
            'taxonomy_version': TAXONOMY_VERSION,
        }


def classify(text: str) -> dict:
    """What kind of failure this is, from whatever the provider said.

    Returns a reason and whether waiting could help. An unrecognised error is terminal, which is the safe
    direction: the retry would spend the budget the degraded answer needed.
    """
    haystack = (text or '').lower()
    for kind in TERMINAL_TYPES:
        if kind in haystack:
            return {'reason': _label(kind, terminal=True), 'retryable': False, 'matched': kind,
                    'taxonomy_version': TAXONOMY_VERSION}
    for kind in RETRYABLE_TYPES:
        if kind in haystack:
            return {'reason': _label(kind, terminal=False), 'retryable': True, 'matched': kind,
                    'taxonomy_version': TAXONOMY_VERSION}
    return {'reason': 'unclassified', 'retryable': False, 'matched': '',
            'taxonomy_version': TAXONOMY_VERSION}


def _label(matched: str, *, terminal: bool) -> str:
    """A reason a person reads, from a type a gateway sends."""
    if matched.startswith(('throttl', 'too_many', 'too many', 'rate_limit', 'rate limit')):
        return 'throttled'
    # The phrase forms as well as the tokens, for the reason the terminal list carries both: what arrives is
    # the provider's message, and a label that only knows the type token falls through to a generic word.
    if matched.startswith('budget') or matched in ('envelope_spend_limit', 'enforced_spend_limit',
                                                   'insufficient_quota'):
        return 'budget'
    if matched in ('invalid_api_key', 'authentication_error', 'permission_denied', 'access_denied'):
        return 'not_authorised'
    if matched in ('validation_exception', 'invalid_request'):
        return 'bad_request'
    if matched.startswith('content'):
        return 'refused_by_provider'
    if matched in ('service_unavailable', 'internal_server', 'overloaded'):
        return 'provider_unavailable'
    if matched in ('timeout', 'connection'):
        return 'unreachable'
    return 'terminal' if terminal else 'retryable'


def degraded(reason: str, detail: str, passages: list, budget: Budget, attempts: int,
             threshold: str | None, partial_output: bool, lane: str = 'frontier') -> dict:
    """The answer when the provider will not give one, and the facts that go with it.

    **The retrieved passages rather than an apology.** Retrieval and generation are already separate halves,
    so a degraded answer carrying cited passages still delivers the client their own material. A try-again
    message throws away work already done, and a cheaper model answer changes quality silently under the same
    branding. The excerpts are passages the session was already permitted to read, so nothing widens access.
    """
    return {
        'kind': 'degraded',
        'reason': reason,
        'lane': lane,
        'detail': detail,
        'passages': [
            {
                'index': n + 1,
                'doc_id': p.doc_id,
                'acl': p.acl,
                'page_start': p.page_start,
                'page_end': p.page_end,
                'excerpt': (p.text or '')[:240],
            }
            for n, p in enumerate(passages)
        ],
        'attempts': attempts,
        'threshold': threshold,
        'partial_output': partial_output,
        'budget': budget.describe(),
    }


def audit_facts(reason: str, matched: str, retryable: bool, attempts: int, threshold: str | None,
                budget: Budget, partial_output: bool, errors: list, lane: str = 'frontier',
                data_class: str = 'internal') -> dict:
    """The row a degraded answer leaves behind.

    Counts, identifiers and classifications. **Never the prompt, a passage, a credential or a raw provider
    string**, because a validation error can echo input fragments and this table is append-only.
    """
    return {
        'stage': 'degraded',
        'reason': reason,
        'matched_type': matched,
        'retryable': retryable,
        'attempts': attempts,
        'threshold': threshold,
        'partial_output': partial_output,
        'lane': lane,
        'data_class': data_class,
        'errors': errors[:8],
        'taxonomy_version': TAXONOMY_VERSION,
        **budget.describe(),
    }

