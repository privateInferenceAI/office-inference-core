# C3: the assistant API, and the decisions behind it

Written 2026-10-14, before the assistant API is built. Six research passes were run over the open questions
because these choices are expensive to reverse, and this records what they changed.

**How to read this.** Each section states the decision, the evidence, and what would make us revisit it. Where
the industry disagrees, both sides are here. The sources at the end are the ones that carried weight.

## 1. Routing stays mechanical, and the gate moves in front of it

**Decision.** The three registered triggers stay: a fixed task list, synthesis across more than one document,
and an explicit request from the user. They choose a model **within a lane**. Which lane is permitted is a
separate, earlier decision made by the data-class gate, and it is a policy lookup against the manifest rather
than a judgement.

**Why.** Two independent results, both from 2026, point the same way.

- A July 2026 evaluation across 290 tasks and 2,610 candidate outcomes found three of four open-source routers
  emit constant or near-constant tier assignments, and the only content-aware one, vLLM Semantic Router, had
  "the highest observed success rate on none of the four benchmarks".
- AWS benchmarked a model-as-router against an always-strong baseline: the router cost USD 1.068 per 1,000
  decisions against USD 0.332 per 1,000 for simply answering with the strong model, roughly three times the
  thing it was meant to save. The same benchmark, code-graded with no model as judge, scored always-cheap at
  80.5 and always-strong at 93.1 out of 100.

**What that means for us specifically.** Every routing cost figure in the literature prices a model call. There
is no published comparison of router-as-rule against always-cheap, because a rule costs nothing to run and
nothing to write down. Our router is arithmetic over signals we already hold, so the AWS cost result does not
apply to it, and the quality result does: a cheap tier alone is measurably worse, so escalation earns its keep.
Both halves of that belong in this document, because the cost numbers will otherwise be read as an argument
against routing at all.

**One correction to our own design.** Distinct `doc_id` count is a proxy for synthesis, not synthesis. It has
false positives, when a query incidentally matches two documents that need no reconciling, and false negatives,
when one long contract needs synthesis internally. It stays, because determinism is worth more here than
accuracy, with two changes: the audit row says the trigger was a proxy test rather than a measured property,
and there is a relevance threshold under it so "two documents" means two that scored rather than two incidental
lexical matches.

**Why the trigger is not where the explanation goes.** A compliance reviewer asks why a question reached an
external model. If the answer is "a heuristic fired", that is a story about code. If it is "this deployment
declares that this document class may be processed externally, and the user's session permitted it", that is a
sentence about a permission the client reviewed and signed. The second is the artifact worth having, and it is
why the gate goes first.

**What would change our mind.** A measured router that beat constant-tier assignment on quality per unit cost
at our scale, on document question answering rather than code, and that remained deterministic enough to audit.

## 2. Sessions pin their tier, because caching and routing fight each other

**Decision.** A session resolves its lane at the start, or at its first escalation, and holds it for the life of
the session. LiteLLM calls this `session_affinity`, and it is opt-in.

**Why.** Provider caches are per-model. Anthropic states that when a request moves to another model, "the
conversation prefix already cached for the first model must be written into the new model's cache from scratch",
and cache writes cost more than cache reads. Their own numbers on a current model: USD 4.00 per million input,
USD 5.00 for a cache write, USD 0.20 for a cache read. So a 20,000-token conversation prefix that escalates on
turn eight pays about USD 0.10 instead of about USD 0.004, and if the next turn returns to the cheap tier, the
expensive prefix goes cold and the next escalation pays again. Their fallback credit reimburses a cache miss
caused by a provider-side fallback inside one request. It does not reimburse our own tier switching.

**Consequence for the register.** The triggers become session-level rather than turn-level. The first escalation
in a conversation pins it, and later turns stay there. That is a change to how the routing step is written, and
it belongs in the runbook when the step is written rather than discovered during it.

**A warning against leaning on caching for engineering reasons.** On Bedrock, implicit caching is documented as
best effort: an identical prompt does not guarantee a hit and hit rates vary. Explicit caching needs cache
checkpoints and a 512 to 4,096 token minimum depending on the model, and any change to the prefix causes a miss.
So caching is a discount that may arrive, never a load-bearing part of our cost model or our latency promises.

## 3. A request has one deadline and three thresholds

**Decision.** One wall-clock deadline per request, because the register and any interface need one number.
Internally: per-tier sub-budgets, and never a sub-budget larger than the remaining deadline. Three thresholds
rather than two, being time to first token, a **stall budget** for the gap between chunks after the first, and
the total cap. Degradation triggers on the remaining budget, not on wall clock: surrender when the remainder is
less than the cost of a first token plus one token, so there is always time to fetch passages, stream them and
write the audit row.

**The stall budget is the one everybody forgets.** A stream that emits a token every 80 seconds passes a
first-token check and then burns the whole deadline with the user waiting.

**The first-token budget must be derived, not chosen.** Prompt processing on CPU is roughly linear in prompt
tokens, so a fixed ten seconds would kill healthy local requests while being absurdly generous for Bedrock.
llama.cpp exposes `prompt_tokens_seconds` and `predicted_tokens_seconds`, so the budget is measured on our own
box and set just above p95 at the largest context we permit. Then bound the CONTEXT rather than the timeout,
because raising a timeout only moves the failure into the total cap.

**The total cap is derived from the longest answer we will deliver**, not from the provider's maximum, with a
**delivery reserve** carved out, because the degraded answer has to be produced and recorded inside the same
budget.

**Keepalive is a correctness requirement for our local tier, not a nicety.** The Anthropic guidance about
streaming anything over ten minutes concerns *idle* connections. During slow CPU prompt processing our
connection is idle, which is exactly the condition intermediaries kill. Without a heartbeat from our own API
through that window, a slow local first token surfaces as a hard connection failure rather than a degraded
answer, and it would be diagnosed as a networking fault.

**One llama.cpp trap worth writing down.** `--parallel` divides the KV cache across slots and on CPU makes slots
contend for the same cores. Raising it improves throughput and wrecks time to first token. We choose the
tradeoff deliberately or rediscover it in production.

## 4. The outcome is three-way, not two

**Decision.** Classify on the provider's error TYPE, never on the HTTP status. Retry only before the first
token, honour `Retry-After`, cap attempts, draw every wait from the single deadline, and on exhaustion return a
typed degraded payload. Bulkhead concurrency with a semaphore. No circuit breaker, no hedged requests, no
queueing service: at five to thirty users, the provider's queue is our queue.

**Why status is not enough.** On Bedrock a 429 covers both `ThrottlingException` (our quota) and
`ModelNotReadyException`, and AWS separates 429 from 503 and 529, which are capacity rather than our quota.
Anthropic returns a spend-cap 429 with `rate_limit_error`, no `Retry-After` header, and docs stating that
retrying fails until access resumes. Retrying on status alone burns the entire deadline on a request that
cannot succeed.

**No circuit breaker, and the reason is concrete.** With one provider there is nothing to break away from, and
an open breaker turns a five-second throttle into a sixty-second outage.

**No cross-lane fallback on failure.** A reliability fallback that moves work from the local lane to the
frontier lane is a data-class decision wearing a reliability costume: a document that was never permitted to
leave the building just left. Degradation moves data toward a LESS exposed lane or it does not move it. Any
fallback that would increase exposure is refused and recorded as a refusal, the same shape as a role supplied
in a request.

**The degraded answer is the retrieved passages, cited, with a typed reason.** Retrieval and generation are
already separate halves, so this is honest and still delivers the client's own data. A "try again" message
throws away work already done, and a cheaper model's answer changes quality silently under the same branding.
Google's SRE book endorses serving degraded results in chapter 22.

## 5. Fault injection: no new component for the throttle

**Decision.** There is no supported way to force a provider error through the LiteLLM proxy. The three
`mock_testing_*_fallbacks` flags were stripped from Proxy requests in v1.85.0 and survive only on direct Router
calls; the one documented mock facility, `mock_response`, mocks success. So the throttle is provoked for real.

**How, and why it is the cheapest option.** AWS deducts `input tokens + max_tokens` from the per-model TPM quota
at admission and throttles when that exceeds it. A request whose input plus `max_tokens` exceeds our quota
produces a genuine `ThrottlingException` on demand, deterministically, with no new component and no blast radius
beyond that one request. A retry with the same oversized `max_tokens` throttles again, which exercises
sustained-throttle-to-degrade rather than retry-then-succeed. Shrinking `max_tokens` between attempts proves the
other path.

**For continuous testing, a stub rather than a product.** One small Python file behind uvicorn returning 429
with Bedrock's error body and a `Retry-After`, reached through `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`, which AWS
documents as highest precedence in endpoint resolution. It covers 429 with and without `Retry-After`, 503, 529,
a malformed chunk and a mid-stream error, which is the whole taxonomy. It is a CI test and not evidence of a real
throttle, and the runbook says which is which.

**Toxiproxy is the wrong tool for this one** and right for the timeout and hard-failure cases. It cannot emit a
429 at all. Keep it out of the throttle evidence.

**The local tier has no throttle and we will not pretend it does.** llama.cpp does not meter anyone. When slots
are full it defers, and `llamacpp:requests_deferred` is the gauge. Its only error is a 503 while the model loads.
So local saturation is a deadline problem and local 503 is the hard-failure case. Manufacturing a 429 from the
local tier would put a fiction in the runbook.

## 6. Requests and responses are recorded separately, and never the text

**Decision.** The audit row records the lane that was permitted, the requested and resolved model separately,
each sub-budget in force, and per attempt the provider error type, status, provider request id, whether
`Retry-After` was present and the backoff actually slept. On surrender it records the remaining deadline, which
threshold tripped, the classification and the rule that decided it. Plus a **`taxonomy_version`**, and a boolean
for whether partial output had already reached the client.

**Why requested and resolved are two fields.** A single model column conflates what we asked for with what ran,
and a route receipt is supposed to record route facts rather than explanations. The route-receipt work names
`requested_model` and `resolved_model` for exactly this reason, and the same pattern appears across vendors:
Bedrock reports its service tier in the response, in CloudTrail and as a `ResolvedServiceTier` metric, and
OpenAI shows a Priority request downgraded to Standard.

**Why `taxonomy_version`.** The chain makes rows undeletable, so in six months an examiner reading a degraded
row needs to know which rule set was in force when it was written. A classification without its version is not
reproducible.

**Why the partial-output boolean.** It is the retry-safety fact. A streamed generation is not idempotent: a
retry after partial output is billed twice and cannot be replayed safely. OpenAI's Agents SDK now refuses to
retry a streamed run once output has started, and Anthropic documents that a refusal can arrive mid-stream
after partial output and that any partial output should be treated as incomplete and discarded.

**What must never be recorded.** Prompt or passage text, because that creates a second copy of client content
outside the retention controls of the document store. Credentials or signatures. Raw unnormalised provider
error strings, because validation errors can echo input fragments. And nothing derived from a role supplied in
a request, since that is the thing we refuse.

**The residency claim becomes provable from the chain alone**, because lane and data class are in the row. That
is the point of recording them, and it is why this is a compliance feature rather than an observability one.

## 7. Credentials: the decision, and how it was settled

**Where it stands.** Per-role virtual keys exist in the gateway's database and a plaintext copy sits in
`rendered/keys.json`. The plan was to mount that file into the API. The argument against it: LiteLLM stores
those keys hashed, so our plaintext copy becomes the crown jewel, and `docker inspect` or a support bundle would
carry every credential.

**The shape being considered.** One signing key delivered as a file, from which the API mints a short-lived
signed assertion per request, bound to the session and the role, which the gateway validates through its OSS
`custom_auth` hook. The gateway then holds no secret the API must fetch, and the only long-lived credential on
the host is one file a rotation can replace. The standard name for the assertion is a transaction token.

**Why this is not decided.** `custom_auth` has enforcement gaps: key-level `max_budget` is unsupported, and the
model allowlist and expiry need `enable_post_custom_auth_checks`. Role budgets would move to team records. That
is a redesign if it goes wrong, so it gets a spike before either shape is committed to.

**A decision with a clock on it, recorded here because it is easy to lose.** `LITELLM_SALT_KEY` is not set.
Without it the master key doubles as the at-rest encryption key for everything in the gateway's database, so the
master key can never be rotated, and setting a salt key later leaves every stored credential unreadable. There
are four virtual keys and spend history in that database today. It is free to fix while there is no client data
and impossible to fix afterwards.

## 7a. The credential decision, resolved by spike on 2026-10-14

**Decision: one signing key, short-lived assertions, and `custom_auth`. The spike says it works, including the
part that would have made it unusable.**

Five questions were put to the pinned version by loading a real hook into a throwaway gateway on the box,
sharing the deployment's database. All five have answers, and two of them are the kind that only appear when
something is actually run.

| Question | Answer |
|---|---|
| Does `custom_auth` load? | Yes. A missing header and a bad signature both return a 401 `auth_error` with the message the hook raised. |
| Does a valid assertion reach the model? | Yes, a real Bedrock answer, and the manifest's declared model was the one called. |
| Does a team budget enforce? | **Yes, and it refuses hard**: `budget_exceeded`, HTTP 422, naming the spend and the ceiling. |
| Does the team have to be a real row? | Yes. A `team_id` the database does not know returns a 404 telling you to create it. |
| Is administration locked out? | **Only if the hook does not handle it**, and this is the finding. |

**The lockout, and the one line that fixes it.** With `custom_auth` enabled the master key goes through the
same hook, so a hook that only understands role assertions refuses the master key too: `/team/list` returned
401 and the whole management surface became unreachable. That would have bricked budget administration, the
`keys` tool and any future `/team/new` call, and it is not mentioned in the documentation the research found.
The hook is handed `api_key`, so it can compare that against `LITELLM_MASTER_KEY` and return a
`proxy_admin` identity. Confirmed: the log shows `matches_master=True` and `/team/list` answering normally
afterwards.

**Two consequences that change the design.**

- **Budgets move from keys to teams.** A key-level `max_budget` is documented as unsupported with
  `custom_auth`, and the spike confirms the identity's key budget is not what enforces. Team budgets do
  enforce, so one team per role carrying that role's ceiling is the shape. The `keys` tool grows from minting
  keys to provisioning teams, and the manifest's `budgets.roles` block is what it reads.
- **This is a stronger budget story than the one we had, and it corrects something we already published.**
  Section 5 of this note records that LiteLLM's key budgets admit against recorded spend, so concurrent
  requests can pass before any is counted. That is true of key budgets. A team budget refused at 422 in the
  spike with the spend and the ceiling named. It is a better ceiling, and it belongs on the team record
  rather than claimed for the key.

**What the spike does not settle.** Whether the token-per-minute ceiling counts concurrent admissions the way
the team budget does, and whether a team's model allowlist is enforced on the assertion path. Both are for the
step, with a test each.

**The spike itself does not ship.** `services/spike/` holds three files that exist to record what was run,
and step 4.2 deletes it once the real hook has its own tests.

## 7c. The credential path, end to end, and the hole it left

Run against the live gateway on 2026-10-14, with the hook in the real gateway image and teams provisioned
from the manifest. Nine checks, and the results split cleanly.

**What works.**

| Check | Result |
|---|---|
| A valid signed statement | 200, a real Bedrock answer from the declared model |
| No statement at all | 401, with the message naming what this deployment expects instead |
| A tampered signature | 401, "the assertion signature does not match the key this gateway holds" |
| A replayed statement | 401, "assertion ... was already presented: a statement is spent once" |
| The admin exemption | 200 from `/team/list` with the master key, which is the lockout avoided |
| A virtual key presented instead | 401. **The gateway no longer accepts long-lived model keys at all.** |
| Team budgets | Provisioned per role from `budgets.roles`, one team each, ceilings reported |

**What the same run found, and it is the important part.** A signed statement for one role reached a model
that role is not scoped to. A `viewer` statement asked for `coder` and got an answer; a `front_desk` statement
asked for `analyst` and got an answer. **The team's model allowlist is not enforced on the custom-auth path**,
even with `enable_post_custom_auth_checks` on and `custom_auth_run_common_checks` on, which is the pair the
documentation names for exactly this. The startup warning speaks only about budgets, model-access allowlists
and rate limits being unenforced when common checks are *off*; turning them on enforced budgets, as the spike
had already shown, and did not enforce the allowlist.

**Why that is a smaller hole than it looks, and still one to close in code rather than in hope.** The API is
the only thing that holds the signing key, and it chooses the model from the manifest for the session's role;
a client never names one. So the allowlist is not load-bearing for the product's behaviour today. What is
wrong is that the manifest's declaration of which model a role reaches is a promise the gateway does not keep,
and a control that exists on paper and not in the path is the shape this project keeps paying for.

**The decision that follows: the API enforces the model, not the gateway.** It resolves the role to a model
from the manifest and requests that, which puts the choice in the one process that already knows the session,
the role and the permit. The gateway's allowlist stays configured, because it costs nothing and might start
working, and step 4.5 proves the mapping by changing it and watching the called model change.

**A second consequence worth writing down.** With `custom_auth` in place the gateway accepts exactly two
credentials: a signed statement, and the master key. Every virtual key minted by the `keys` tool is now inert
for request traffic. They still exist, they are still scoped and budgeted, and nothing presents them, so the
next step should either find them a use or stop minting them rather than leave a security control that has
quietly become decoration.

**And one correction to section 8 below.** "Do not describe gateway budgets as hard caps" was recorded from
the research, and it was about key budgets. The team budget refused at HTTP 422 in the spike with the spend
and the ceiling named, and it is enforced through the path a statement takes. The earlier correction still
stands for keys; it is now wrong to apply it to teams. The manual and the schema get this right when the API
page is written.
## 7d. The API, and the two things the first probe found

Six rights held on the first clean run, and two findings behind them, both worth keeping because both were
invisible to reading.

**Finding 1, fixed: every audit write the API made was being refused.** The audit tool refuses a hand-typed
actor unless `--dev` is passed, and its guard fires before the database is touched. That guard is right in
spirit and was wrong for this caller: the actor the API passes came from a session row the process resolved,
so nobody typed it. The first probe run passed its FORGED right **on a denial row written by an earlier run
rather than by the request under test**, which surfaced only because the probe counts rows instead of trusting
a 403.

**The fix is the shape the guard was asking for.** The API signs a statement naming the actor it resolved,
with the signing key it already holds, and the audit tool verifies the signature and takes the actor from
inside it. The development flag is gone from the deployment. A statement minted for a model call cannot write
an audit row and the reverse is also refused, because the purpose is inside the signed payload and checked on
the far side.

**Three defects found while building that fix**, each a small thing that would have been a quiet failure:

1. `verify()` required a `role`, so a genuine audit statement was refused for a field it was never supposed to
   carry. Authenticity and purpose are two questions, so there are two functions now: one proves a statement is
   genuine, and each caller checks the payload it needs.
2. `--actor` was still required by the argument parser, so every signed record died in argparse before the
   guard was reached. The parser does not require it and the guard refuses the case where neither is present.
3. The API mounts the audit tool into `/app` but not the verifier beside it, so the tool could not check a
   statement at all. It refuses loudly rather than writing an unverified row, which is the right direction and
   was invisible until the log was read.

**Verified on the live deployment**: a question over HTTP returns a cited answer, the API log holds zero
audit-write failures, and the chain holds a `model_call` row whose actor came from the session and whose model
is the one the manifest declares.
**Finding 2, fixed the same day: the team budget did not enforce, and the reason was the spike.**

**The spike proved something other than what it looked like.** Its hook returned `max_budget=0.000001` on the
identity itself, and what refused at 422 was that number being spent, not the team's ceiling. The team row was
decorative in the spike, and I read it as load-bearing and wrote it into this document as a stronger budget
story than the key budgets it replaced.

**The gateway does not read the team row for an identity that came from a custom-auth hook.** So the hook
computes the remainder itself: it reads the team's ceiling and spend, returns `ceiling - spend` as the
identity's `max_budget`, and lets the gateway accumulate spend against the same row. A ceiling that cannot be
read is a refusal rather than an allowance, because a database that is unreachable must not turn a budget into
an unmetered lane.

**Verified on the live deployment**: spend 99 against a ceiling of 10 returns a 502 whose body names the team,
the current cost and the maximum; spend 0 against the same ceiling returns a cited answer.

**A second finding, and it is operational.** LiteLLM caches the team row. Editing the spend directly in the
database and asking again still reports the old figure until the gateway restarts. That is harmless in normal
operation, because the gateway is the thing that writes the spend, and it matters for anybody testing a
ceiling by hand: **the number in the database is not the number the gateway is enforcing.** The probe says so
where it measures it.

**This is the third control in this phase found reporting success while doing nothing**, after the audit writes
and the model allowlist, and the first one fixed. The pattern is worth naming: none was visible to reading the
code, and each was obvious within a minute of running it.
**What this pair has in common, and why it is the useful part.** Both are controls that reported success
while doing nothing: an audit write that failed loudly into a log nobody was reading, and a budget that
returned an answer. Neither would have been found by reading the code, and the second was found only because
the probe spent the money rather than checking the config.
## 7e. Streaming redaction, settled by running the gateway

**The question.** Can a guardrail block on a streamed response, or is it audit-only? A researcher reported from
documentation that LiteLLM's `post_call` guardrails cannot affect a stream, and that a setting named
`stream_holdback_chars` exists for blocking redaction. The second half of that was wrong in an interesting way,
so the whole thing was put to the running gateway.

**What a spike on the box established, by registering a custom guardrail that observably rewrites text and
comparing a streamed request against a non-streamed one:**

- **The legacy `post_call` success hook runs on a streamed response and cannot affect the client.** It fires
  after the streaming iterator has ended, so the status line and every content byte are already on the wire.
  The claim was right.
- **`post_call` is a mode, not a mechanism, and that is the correction.** A guardrail that overrides
  `apply_guardrail` *does* change streamed bytes, provided `streaming_transform_mode` is `incremental_diff`
  and it returns a holdback. Under the default `block_only` the rewrite is computed and then silently
  dropped, which is the worst of both.
- **The holdback is load-bearing rather than decorative, and its absence does not fail quietly.** An A/B with
  a redactor that cannot decide until later text arrives: with the holdback, the client received one frame
  containing only redacted content. Without it, the raw prefix went out and the stream then failed with a 400
  because emitted bytes cannot be retracted. It fails closed, which is the right direction, and the prefix is
  still gone.
- **`stream_holdback_chars` is not a configuration key.** It is a value the guardrail returns per round, and it
  has to be returned on **every** round including the ones that change nothing. Withholding only when a
  rewrite happens is exactly what produces the leak above.

**Why none of that changed the design, and what it confirmed.** The API already redacts in its own writer,
which the spike's conclusion endorses: redaction belongs inside the byte-emitting path, and if the service
streams to the browser itself it must hold its own window. What the spike added is the property that the
holdback is unconditional. That is now a test, because it is the difference between the working case and the
leaking one, and it would otherwise have been a line of code somebody later optimised into a bug.

**The gateway keeps its guardrails for the audit record**, which is what the legacy hook is genuinely good at:
it sees the assembled response after delivery, which is what a row wants. It is not the control.

**And a claim in section 9 needs narrowing.** "Redaction must block in-stream" was recorded there as something
to verify. It is verified, and the answer is narrower than the wording: **the API's own writer must block
in-stream.** A gateway may or may not, depending on which hook it uses and which mode it is in, and a control
whose behaviour depends on two settings nobody reads is not one to promise a client.
## 7f. The audit write, measured, and what the measurement rules out

**The measurement.** Nine streamed requests against the live deployment gave a time to first token of 231ms at
the minimum, 260ms at the median and 359ms at p95, with retrieval between them costing 0 to 9ms. Total time ran
498ms to 2904ms. Those are the numbers `4.4`'s budget is built from, and they are for the **frontier lane**:
this deployment answers from Bedrock, and the office build's local llama.cpp server will need its own figures,
where prompt processing is compute-bound on CPU and the first-token time is a different shape entirely.

**Then the anomaly.** Wall-clock time exceeded what the server reported by 350 to 650ms. Chasing it produced
the finding: the API writes its audit rows by starting a Python interpreter, and the breakdown is

| Piece | Cost |
|---|---|
| interpreter start | 9 ms |
| `import psycopg` | 124 ms |
| importing the audit tool | 10 ms |
| the write itself | 17 ms |

**The driver import is the cost, and it cannot be optimised away.** The modules are already precompiled in the
image, and a warm import still costs about 130ms, so this is psycopg's own import graph rather than compilation.
Any subprocess that writes a row with that driver pays it. This is what rules out the cheapest-looking fix:
there is no "make the subprocess cheap" option, only "do not spawn one".

**Where it sits, which is worse than assumed at first.** Both paths block. In the non-streaming handler the
write is before the response is returned. In the streaming handler it is before the `done` frame. So every
request waits roughly 160ms for a record to be written after the model has already finished.

**The decision: keep the subprocess, and defer a success row's write off the response path.** State the cost
and accept it. The alternative considered was a resident writer service appending over a Unix socket, which
makes a write microseconds and is what would make the two-write design below affordable.

**A fallback to a subprocess alongside a resident writer was considered and rejected for now**, and the reason
is worth recording because it is not obvious. A fallback that writes the row itself creates a **second writer**,
and two rows computed from the same predecessor hash fork the chain: the verifier then reports a break that is
indistinguishable from tampering. The failure that bites is not the writer being dead but the writer being
*slow*, because the API times out, writes directly, and then the writer finishes its append.

**If it is built, the serialization belongs in the database rather than in the process topology.** The writer
holds a Postgres advisory lock while appending; the fallback tries the same lock and waits if the writer holds
it. Then there is never a second writer whichever path runs, and the invariant is enforced by the component that
can enforce it atomically. That also turns "unavailable" into "cannot acquire the lock", which is a fact rather
than a timeout guess.

**The gap this leaves, and it is the real reason to build the writer.** The research design for `4.4` is
write-ahead: a refusal recorded before the error returns, an intent row before the provider call, a completion
row after. What exists is **one row per model call, written after it finishes**. The intent row is the one that
has to be synchronous, because it is evidence that a call was permitted before it happened. Two writes per
request at 160ms each is not affordable with a subprocess and is nearly free with a resident writer.

**One decision left open deliberately.** If both paths to the audit are unavailable, refusing the request is
fail-closed and right for a compliance product, and it turns one broken component into an outage; spooling
locally keeps the product up and makes the record eventual. That choice belongs with whoever builds the writer,
and it is recorded rather than guessed.
## 7g. Provoking a throttle, and a recommendation that did not survive being tested

**The recommendation was wrong and the fix is recorded here.** Section 5 of the defect list above proposed
provoking a genuine provider throttle through Bedrock's admission arithmetic: an oversized `max_tokens` would
deduct more than the per-model quota at admission and earn a real `ThrottlingException`, deterministically, with
no new component.

**Bedrock validates `max_tokens` against the model's limit before any quota arithmetic.** Measured:

    max_tokens=4200000 -> 400 BadRequestError: The maximum tokens you requested exceeds the model limit of
                              10000. Try again with a maximum tokens value that is lower than 10000.

**The quotas themselves were then read**, and they vary by two orders of magnitude:

| Model tier | Tokens per minute |
|---|---|
| Nova Lite, on-demand | 4,000,000 |
| Nova Pro, on-demand | 1,000,000 |
| **Nova Pro V1, latency-optimized on-demand** | **40,000** |
| AI21 Jamba 1.5 Large | 30,000 |

**So a real throttle needs load, and the cheapest honest route is the smallest quota rather than the biggest
request.** Nova Lite at four million tokens per minute would take roughly four hundred maximum-size requests;
the latency-optimized Nova Pro tier at forty thousand takes about four, because a single request may ask for up
to ten thousand. Same provider, same classification path, a ceiling that is reachable.

**What is still unverified, and it is the part that could waste a day:** which field LiteLLM 1.104.2 passes to
Bedrock to select that tier. A wrong field would silently use the standard quota, produce no throttle, and look
like a budget bug.

**The lesson is the one this phase keeps teaching.** The admission-arithmetic method was the cheapest-looking
option, it was recommended on the strength of vendor documentation, and it took one request costing nothing to
disprove. It had been carried into the runbook as though it were settled.
## 7h. Two decisions taken on 2026-10-14

### Escalation is announced, always

**Decided: a user is always told what is happening.** When a request asks for the stronger model and the
deployment's data class permits that lane, it escalates and the answer says which model produced it. When the
data class forbids it, the answer comes from the strongest model the content **is** permitted to reach, and it
says that too: that the stronger model was asked for, that this content may not reach it, and which model
answered instead.

**Why not refuse.** Refusing is equally honest and less useful. The user asked a reasonable question and would
get nothing, and they could not tell that apart from a permission problem. Announcing does the same work as a
refusal while still delivering an answer.

**Why not downgrade quietly.** That is the silent subversion the mechanical design exists to prevent: the user
believes they got the stronger model, when the content never left the building. It also makes the audit record
the only place the substitution exists, which means the person affected is the last to know.

**The one-way boundary holds either way.** A user cannot move content to an external lane by asking. The
escalation is bounded by the data-class gate before the trigger is even consulted, and the announcement is what
makes the bound visible rather than mysterious.

**Recorded as two facts, not one:** the escalation was requested, and the lane that answered. A single field
would conflate what was asked for with what ran, which is the same distinction the audit row already draws
between requested and resolved model.

### The audit writer: build it, spool locally, and refuse past a bound

**Decided: build the resident writer, and on failure spool rather than refuse immediately.** Section 7f left
this open and it is settled here.

**Why not refuse outright.** Fail-closed sounds correct and turns a logging problem into an outage. A client's
document assistant stopping because the audit writer hiccupped is not defensible, and it is the kind of coupling
that gets a component ripped out rather than fixed.

**Why not spool without a limit.** The deployment would run indefinitely with no record, precisely during the
incident somebody will later need the record for.

**So: spool to disk, and refuse only past a bound.** A short outage costs nothing visible; a sustained one stops
the product, because unbounded unrecorded operation is the thing this system exists not to do. **The bound is
the policy knob**, and it belongs in the manifest beside the other client declarations rather than in code.

**The serialization is unchanged and is the part that must not be got wrong.** The writer holds a Postgres
advisory lock while appending and the fallback attempts the same lock, so there is never a second writer
whichever path runs. A naive fallback that writes the row itself forks the chain, and the verifier then reports a
break indistinguishable from tampering. The failure that bites is the writer being *slow* rather than dead.

**What it unlocks, and the reason it is worth the component.** The research design is write-ahead: a refusal
recorded before the error returns, an intent row before the provider call, a completion row after. What exists
today is one row per model call, written after it finishes, at about 160ms each because the driver import
dominates. Two writes a request is unaffordable that way and nearly free with a writer.

**Not built yet.** It is the first thing after C3's remaining step, and the failure mode is decided so whoever
builds it does not have to choose under pressure.
## 7i. What the field does, checked on 2026-10-14

**A correction first.** Section 7e says `stream_holdback_chars` "is not a configuration key". It is a real
LiteLLM parameter: a per-guardrail value for how many trailing characters to withhold so a match cannot be split
across two chunks. Verified in the pinned image, where it lives in `custom_guardrail`. What is true is the
narrower claim that a **guardrail supplies it per text per choice** rather than the transport hardcoding it, and
that withholding only on a rewrite leaks the prefix. The distinction matters: the parameter exists, and our
holdback is a reimplementation of a supported feature rather than something the vendor lacks.

**Why we keep ours anyway.** It lives in code an auditor can read, on a pin whose version was until recently
unrecorded and which has reported fail-open branches in exactly this path. A control we own and can test beats a
configuration knob we do not. LiteLLM's own default for a Bedrock guardrail is
`streaming_buffer_until_moderated: true`, which buffers every chunk, and its documentation describes turning
that off as detect-and-log rather than prevention.

### Where the field is, and where we are

| | Their posture | Ours |
|---|---|---|
| Cloudflare AI Gateway DLP | buffers the whole response; docs say time to first token rises in proportion to generation time | we stream |
| AWS Bedrock Guardrails | masking requires sync mode, which buffers; async mode "doesn't support the masking of sensitive information" | we stream |
| LiteLLM + Bedrock guardrail | buffers until end-of-stream moderation by default | we stream |
| Portkey | no redaction at all: a boolean verdict, deny or log | we rewrite |
| Kong AI PII Sanitizer | redacts, enterprise tier, stream behaviour undocumented | we redact, and document it |

**So full buffering is the majority answer and we deliberately differ.** That is a real tradeoff rather than a
win: buffering is provably correct and kills streaming, and we chose streaming plus a bounded proof. The
research found **no published measurement of a settlement-point redactor's time to first token**, so our claim
that the cost is small is unevidenced and everyone else's is too.

### The failures the field keeps shipping, and which of them we have

| Failure | Who shipped it | Ours |
|---|---|---|
| withheld only when a rewrite happened, leaking the raw prefix | Mastra | **not present.** Our holdback is unconditional, and a test asserts it |
| holdback size a magic number with no guard | Mastra used 128 and documented it as insufficient | **not present.** Ours is derived from the longest pattern and a test fails if a longer one is added |
| buffer mutated in place, deleting ordinary text on overlapping detections | Mastra | **not present.** The raw buffer is redacted, and an emitted marker is never rescanned |
| audit trail claims success while the stream was not scanned | LiteLLM issue 35257 | **unverified.** Not yet adversarially tested, and it is the failure that would matter most to an auditor |
| fail-open on a redactor exception | reported in three LiteLLM paths | **unverified.** A catch that falls through to emitting would be the same bug |
| cache hits skip re-scanning after a policy change | Cloudflare | not applicable, we do not cache responses |

**The count worth recording:** four streaming stacks sharing no code shipped the split-boundary leak between
February and September 2026, in TypeScript and Python, across proxies, agent frameworks and SDK middleware. The
directional finding is corroborated by four independent bug reports even though the benchmark that counts them
is vendor-published.

### What we are behind on, and it is not the mechanism

**Chunk-invariance is a property and we test a proxy for it.** Our unit test asserts that no pattern is longer
than the holdback, which guards the day somebody edits the pattern list. It does not assert the property itself:
that redacting a stream gives the same output for every possible way the text could be split. `chunk-invariance`
(Apache-2.0, standard library only, offline) does exactly that in one assertion and reports the smallest failing
split. That is a straight upgrade and it is the first thing to adopt from this research.

**Unbounded entities are covered by nobody, on the output side.** Presidio's own maintainers state it: a fixed
window can always be crossed because some entities have no maximum length. Every credible production answer is
on the request side, either redact-before-model and rehydrate, or stop streaming. Our posture is to redact the
request for bounded patterns and accept that a name the model introduces is best-effort.

**The answer that fits us is a client entity list, and nobody sells it.** Enumerate the names and addresses that
matter for this deployment, redact them deterministically on the way in, and skip open-set NER entirely. It costs
a maintained list and no ML runtime, and an auditor can read the list and re-run the test against it. A CPU
Presidio gives recall numbers we could not defend and 600MB to keep patched.

**The office build is the one to check.** It runs a local model with no gateway guardrail, so any control
implemented at the gateway is absent there. The holdback sits in the API, which both legs pass through, and that
is the property to keep.
## 8. What we are deliberately not building

- **A separate router process.** One Python process with routing behind a pure function. The pure function is
  the compliance artifact and the extraction seam; the process split buys nothing at one provider and thirty
  users.
- **Multiple Uvicorn workers.** LiteLLM registers shared background jobs once per worker, and with no Redis to
  elect an owner they run unguarded in every process.
- **Protocol-level stream resumability.** MCP removed it in its July 2026 revision. Replay comes from our own
  run table.
- **A vector database per permission set.** One team moved there for about 40 percent more storage. Our filter
  is inside the query, which is the cheaper form of the same guarantee, and our denial policy is the client's
  declared choice.
- **A secrets manager, an outbox, or a message broker.** Named here so that adding one later is a decision with
  a reason rather than a drift.

## 9. Findings that are not decisions but change a later step

- **Redaction must block in-stream.** A `post_call` guardrail runs on the assembled response after delivery, so
  on a streaming path it audits and cannot block. If that holds for our pin, a PII pattern in a streamed token
  reaches the browser before anything can redact it. **Verify against our deployment before the streaming
  step.**
- **Never trust the gateway's usage numbers for audit.** Providers send usage in the final chunk, so an early
  client disconnect loses it. Count tokens from the deltas we already have.
- **Ingress headers from the start.** `Incremental: ?1` (RFC 10036, August 2026) plus `X-Accel-Buffering: no`.
  The nginx buffering failure turns 0.41s at the origin into 3.4s at the client and is invisible to load tests.
- **A policy revision in the audit row**, so "which human caused this retrieval, under which policy" is
  answerable.
- **Snapshot for the record, enforce live.** Our session snapshots the document roots it resolved, which keeps
  an old audit row accurate. The Zanzibar "new enemy problem" is the matching hazard: a snapshot must not be
  what a request is authorised against. The session's roots are the record; the live manifest decides the
  request. Both properties, in that order.
- **Denial shape.** Google's AIP-211 argues explicitly against 404-instead-of-403 and specifies a 403 saying the
  permission was denied "or it might not exist". Worth matching when the refusal wording is finalised.

## Sources that carried weight

- [Task- and Session-Level Model Routing, four routers on four benchmarks (arXiv 2608.14641)](https://arxiv.org/abs/2608.14641)
- [Benchmarking Strands Decider, LLM classifiers and Bedrock routing (AWS Builder Center, Oct 2026)](https://builder.aws.com/content/3KRx3G8JMf1G06ZFu2dDEdE2DLw/benchmarking-strands-decider-llm-classifiers-and-bedrock-routing-for-model-routing)
- [Model Routing as a Trust Problem: route receipts (arXiv 2605.01710)](https://arxiv.org/abs/2605.01710)
- [Where Does Agent Reliability Come From? (arXiv 2607.17044)](https://arxiv.org/abs/2607.17044)
- [Spanda: zero-cost lexical entropy matches neural semantic uncertainty (Zenodo, Sep 2026)](https://zenodo.org/records/22233648)
- [When to Trust the Cheap Check (ICML 2026)](https://icml.cc/virtual/2026/poster/62754)
- [Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) and [refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback)
- [Bedrock prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html), [token burndown in quota management](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html), [API error codes](https://docs.aws.amazon.com/bedrock/latest/userguide/troubleshooting-api-error-codes.html)
- [LiteLLM: reliability and which mocks were stripped](https://docs.litellm.ai/docs/proxy/reliability), [auto routing and session affinity](https://docs.litellm.ai/docs/proxy/auto_routing), [production practices](https://docs.litellm.ai/docs/proxy/prod), [custom auth](https://docs.litellm.ai/docs/proxy/custom_auth), [master key rotation and the salt key](https://docs.litellm.ai/docs/proxy/master_key_rotations)
- [Google SRE Book, addressing cascading failures](https://sre.google/sre-book/addressing-cascading-failures/)
- [RFC 10036, incremental forwarding of HTTP messages](https://www.rfc-editor.org/rfc/rfc10036.txt)
- [MCP SEP-2567, sessionless MCP](https://modelcontextprotocol.io/seps/2567-sessionless-mcp.md)
- [OWASP AISVS access controls on RAG indices](https://github.com/OWASP/AISVS)
- [Google AIP-211 authorization checks](https://google.aip.dev/211)
- [Authzed on the Zanzibar new enemy problem](https://authzed.com/docs/spicedb/concepts/zanzibar)
- [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)
