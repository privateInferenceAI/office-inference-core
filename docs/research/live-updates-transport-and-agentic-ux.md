# Live updates for long-running agentic work: transport decision and interface principles

Research report, 2026-10-16. Written to inform the phase that adds multi-step agentic work with an
approval gate, and the Review screen that goes with it.

The transport question is answered first because it is the expensive one to reverse. Sections 2
through 5 cover how the work should look while it runs, how an approval gate should be presented,
and how a client recovers after a disconnect.

Every claim below carries a source. Where I am reasoning from an adjacent domain rather than direct
evidence, I say so in the sentence.

---

## 1. The recommendation, stated first

**Keep Server-Sent Events. Do not add WebSockets. Do not return to polling.**

`GET /jobs/{job_id}/events` is already the right shape: an `EventSourceResponse` over an append-only
ordered log, with `?since=<seq>` replay. Three of the four properties this design needs are already
in it or in the library underneath it, and I verified each against primary sources rather than
assuming.

Three things should change, and none of them is the transport:

1. **Send a real event `id:` on every frame.** The endpoint currently puts `seq` in the JSON body but
   does not set the SSE `id` field (verified in `services/api/app.py`, the `replay()` generator inside
   `job_events`). Setting `id: <seq>` makes the browser's own reconnect send `Last-Event-ID`
   automatically, which turns replay from something the client must remember into something the
   protocol does. This is the single highest-value change in this report.
2. **Make the heartbeat explicit rather than inherited.** `sse_starlette` defaults
   `DEFAULT_PING_INTERVAL = 15` and sends a comment line, so the jobs endpoint already gets a
   heartbeat today. It gets it by library default, not by declaration. I read the source to confirm
   this because it is not obvious from the call site. Pin `ping=` on the jobs endpoint the way
   `/ask/stream` already does, so a future refactor cannot silently drop it.
3. **Never let the client be the only place a run's progress lives.** The client should be able to
   reconstruct the screen from `GET /jobs/{id}` alone, then attach the stream. Details in section 5.

The conditions under which this recommendation is wrong are in section 1.6. They are narrow but real.

---

## 2. Why SSE is the right transport here

### 2.1 The decision is one-way, and SSE is the one-way protocol

MDN states the case directly: "Unlike WebSockets, server-sent events are unidirectional; that is, data
messages are delivered in one direction, from the server to the client... That makes them an excellent
choice when there's no need to send data from the client to the server in message form."
([MDN, EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource))

This product's update channel is exactly that. The client watches a job. Control actions (pause,
resume, cancel, approve) are already ordinary `POST /jobs/{job_id}/{action}` calls, and they should
stay that way. Sending them over a duplex socket would make every control action asynchronous and
unacknowledged, which is the opposite of what an audited system wants: the audit row and the HTTP
status code belong in the same round trip.

WebSocket support also costs configuration that SSE does not. The nginx documentation notes plainly
that "WebSocket proxying requires special configuration and is supported since version 1.3.13"
([nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)), meaning an
`Upgrade`/`Connection` header map that must be repeated in every deployment. SSE needs no equivalent
because it is ordinary HTTP.

### 2.2 Reconnect and replay are in the protocol, not in application code

The HTML Living Standard specifies browser reconnect behaviour: on a dropped connection the client
sets its ready state back to `CONNECTING`, fires `error`, waits the reconnection time, and then
refetches. If the stream previously delivered any `id:` field, the client sets a `Last-Event-ID`
request header on that reconnect. The `retry:` field lets the server set the reconnection delay, and
a `204 No Content` response tells the client to stop reconnecting entirely.
([WHATWG HTML Standard, section 9.2](https://html.spec.whatwg.org/multipage/server-sent-events.html))

That maps onto `seq` almost exactly. Every event already carries a monotonic per-job number. Adding
`id: <seq>` means a laptop that sleeps and wakes reconnects with `Last-Event-ID: 41` and the server
replays from 42 without the client doing anything. WebSockets have no equivalent: reconnect, resume
position, and backoff all become application code that a second repository must reimplement and keep
in sync. Long polling has the same problem.

There is one gap worth naming. The spec requires the client to send `Last-Event-ID`, and it will,
but the server must accept it. A client built by another team may also choose `?since=` explicitly.
Support both, prefer the header when present, and document which wins.

### 2.3 The proxy and load balancer facts, with numbers

This is where the deployment risk actually lives, so I checked each layer against vendor
documentation.

| Layer | Default that matters | What it does to an idle stream | Source |
|---|---|---|---|
| nginx | `proxy_buffering on` | Holds the response in buffers instead of forwarding it as written, which stalls a stream | [nginx proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html) |
| nginx | `proxy_read_timeout 60s` | "If the proxied server does not transmit anything within this time, the connection is closed" | same |
| AWS ALB | idle timeout 60s | Closes a connection with no data in either direction | [AWS ALB attributes](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html) |
| Cloudflare | roughly 100s idle | Documented as the reason long-lived streams need traffic | third-party summary, see note below |

The mitigations are all already correct or cheap:

- **nginx buffering.** The response sends `X-Accel-Buffering: no`, and nginx documents that
  "Buffering can also be enabled or disabled by passing 'yes' or 'no' in the 'X-Accel-Buffering'
  response header field." `sse_starlette` sets this header by default, and the app sets it again
  explicitly. The caveat to know: a deployment can neutralise it with `proxy_ignore_headers`, which
  lists `X-Accel-Buffering` among the fields it can disable. If a client deployment ever shows
  events arriving in bursts, that directive is the first place to look.
- **Idle timeouts at 60s.** The WHATWG spec anticipated this: "Legacy proxy servers are known to, in
  certain cases, drop HTTP connections after a short timeout. To protect against such proxy servers,
  authors can include a comment line (one starting with a ':' character) every 15 seconds or so."
  `sse_starlette` does exactly this, and a comment line is ignored by the client per the spec's
  parsing rules. A 15 second heartbeat sits comfortably under a 60 second timeout at two layers.
- **ALB specifics.** The ALB documentation adds a detail that matters here: "Application Load
  Balancers do not support HTTP/2 PING frames. These do not reset the connection idle timeout." So
  the keep-alive must be application-level data, which is what the heartbeat is. An HTTP/2 ping would
  not have saved this design.

I could not fetch Cloudflare's own timeout documentation; the 100 second figure comes from a
third-party knowledge base article
([stackharbor](https://stackharbor.com/en/knowledge-base/cffix-websockets-sse-behind-cloudflare/))
and should be treated as unverified. It does not change the design, because the recommended heartbeat
is 15 seconds either way. If Office Inference deployments ever sit behind Cloudflare, verify the
figure directly.

**Corporate networks.** The honest answer is that TLS-inspecting proxies are the least predictable
layer and I found no authoritative study of how often they break long-lived HTTP streams. The
structural argument for SSE is that it is a long-lived ordinary HTTP response over port 443 with a
`text/event-stream` content type, so a proxy that passes normal HTTPS traffic has no special case to
apply. A WebSocket requires the proxy to understand a protocol upgrade, which is a distinct code path
that some inspection appliances handle poorly. I am extrapolating from protocol structure here, not
citing a measurement.

### 2.4 Authentication is the one real SSE constraint, and this API already solves it

The `EventSource` constructor accepts only `withCredentials`. The interface has no header option at
all; the IDL is `dictionary EventSourceInit { boolean withCredentials = false; };`
([WHATWG HTML Standard, section 9.2.2](https://html.spec.whatwg.org/multipage/server-sent-events.html)).
So a browser-native `EventSource` cannot send `X-OI-Session`.

This is the constraint that pushes most teams to a `fetch`-based SSE polyfill, cookies, or a query
token. The usual query-token workaround has a real cost: tokens in URLs land in server access logs,
proxy logs, and browser history, which is why OWASP guidance treats URL-borne credentials as a
leakage risk.

This API already accommodates the browser. `_session_for` in `services/api/app.py` resolves a session
from a JSON body, the `X-OI-Session` header, **or the `session` query parameter**. That third path is
what makes a stock `EventSource` work with no workaround. Three consequences worth writing down in the
contract, because a second repository will rely on them:

- The query-parameter path exists for the stream endpoint and should be documented as such rather
  than left as an implementation detail.
- Because the token ends up in a URL, the deployment must not log full request URLs for this route,
  and should prefer a short-lived, stream-scoped token over the long-lived session credential.
- A cookie session would be the cleaner answer on the merits, because `withCredentials: true` sends
  cookies automatically and they stay out of the URL. That is a larger change than this phase needs,
  and I would not make it now. Worth revisiting if the query token ever has to be long-lived.

### 2.5 The connection limit, and why HTTP/2 defuses it

MDN is blunt: "When not used over HTTP/2, SSE suffers from a limitation to the maximum number of open
connections, which can be specially painful when opening various tabs as the limit is per browser and
set to a very low number (6). The issue has been marked as 'Won't fix' in Chrome and Firefox... When
using HTTP/2, the maximum number of simultaneous HTTP streams is negotiated between the server and
the client (defaults to 100)."
([MDN, EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource))

The spec itself offers mitigations, including sharing one `EventSource` across tabs via a shared
worker ([WHATWG, section 9.2.7](https://html.spec.whatwg.org/multipage/server-sent-events.html)).

For this product the limit is unlikely to bite, and it is worth being precise about why. The users are
5 to 50 people in one office, and the design has one stream per open job view, not one per screen.
Six concurrent streams per browser per origin means six simultaneously watched jobs in one browser.
That is not a realistic pattern for a practice manager. The mitigations to keep in reserve, in order:
serve over HTTP/2 so the limit becomes 100 streams, close the stream when the job reaches a terminal
state (the code already has a `TERMINAL` set for exactly this reason), and only then consider a shared
worker. Note that HTTP/2 does not by itself fix a buffering proxy; the two problems are independent.

### 2.6 Conditions under which this recommendation would be wrong

State these in the decision record, because they are the falsifiers.

- **The client needs to send frequent low-latency messages on the same channel.** If review ever
  requires live collaborative annotation on a running job, or the agent needs mid-run steering rather
  than discrete control calls, SSE stops being sufficient. Discrete `POST` actions are fine; a
  conversation is not.
- **One browser must watch many jobs at once.** If the product grows a wallboard showing 20 live jobs,
  the per-host stream limit becomes real. The fix at that point is one multiplexed stream carrying
  many jobs, not one WebSocket per job.
- **A required proxy actively breaks `text/event-stream`.** If a specific client's inspection
  appliance buffers or blocks it and cannot be configured otherwise, the fallback should be short
  polling against the same `?since=` endpoint, not WebSockets. The endpoint is already replayable, so
  polling is a degraded mode rather than a rewrite. This is the strongest argument for the current
  design: the log makes the transport replaceable.
- **Sub-second bidirectional latency becomes a product requirement.** It is not one today, and the
  audit model argues against it, since every step must be recorded before it can be shown.

### 2.7 Why not the alternatives, in one line each

- **WebSockets:** more operational surface (upgrade headers at every proxy, sticky sessions at the
  load balancer, a separate reconnect and replay implementation) for a channel that carries one-way,
  low-frequency, already-persisted events.
- **Long polling:** implements by hand the reconnect and replay that SSE gives free, and turns a
  blocked server-side read into a held connection per client.
- **Short polling:** the only option that needs no persistent connection, and therefore the right
  fallback. It is wrong as the primary because it either wastes requests or adds latency, and it
  cannot express "something just happened" without a delay.

The event rate settles this. Agent step boundaries are infrequent, maybe tens of events over a
30 minute run. That is far too sparse to justify a duplex protocol and frequent enough that polling
would feel laggy.

---

## 3. What actually makes an agentic interface feel fast

### 3.1 The classical thresholds still hold, and they are about real latency

Nielsen's three limits, unchanged since 1993 and explicitly reaffirmed for web applications:
0.1 second to feel instantaneous, 1.0 second to keep the user's flow of thought uninterrupted, and
10 seconds to keep attention on the task.
([NN/g, Response Times: The 3 Important Limits](https://www.nngroup.com/articles/response-times-3-important-limits/))

Two details in that article matter more for this product than the headline numbers.

First, for work of unknown duration, Nielsen recommends a specific pattern: "it may not be possible
to use a percent-done indicator, but it is still possible to provide running progress feedback in
terms of the absolute amount of work done. For example, a system searching an unknown number of
remote databases could print the name of each database as it is processed." An agent job has no
knowable percentage complete. This sentence is the licence to show named steps rather than a bar.

Second, a warning against over-animating short waits: for operations between 2 and 10 seconds, "a
true percent-done indicator may be overkill and, in fact, putting one up would violate the principle
of display inertia (flashing changes on the screen so rapidly that the user cannot keep pace or feels
stressed)."

### 3.2 Time to first token is not simply "faster is better", and the evidence is stronger than expected

This is the finding most likely to be counterintuitive to an engineering team, so the citation
matters. A CHI 2026 controlled experiment (n=240, between-subjects) varied time-to-first-token at 2, 9
and 20 seconds while holding the post-first-token streaming rate fixed at 25 tokens per second.
Participants who experienced the 2 second latency rated the model's output as **less thoughtful and
less useful** than those who waited 9 or 20 seconds. The authors conclude that "latency is not simply
a cost to reduce but a tunable design variable with ethical implications."
([Tan et al., CHI '26, via ar5iv](https://ar5iv.labs.arxiv.org/html/2604.06183))

Qualitative data explains the mechanism: participants read longer waits as "deliberation". The same
paper cites prior work showing that simple framing cues such as displaying "Analyzing your request..."
during a pause increased perceived transparency and trust, and that temporal consistency matters
because deviations from an established rhythm erode confidence.

Two cautions before applying this. The study is about a chat answer, not a monitored job, so
extrapolating it to the Activity or Do screen is an inference, not a replication. And a real product
must not deliberately add latency: the finding is that a wait which is *explained* reads as work,
while an unexplained instant answer can read as a shallow one.

The practical reading for Office Inference is different from what it would be for ChatGPT. Nobody
wants the Do screen to be slow. What the finding argues is that when the work genuinely takes 30
minutes, the interface should present that as deliberation over the document rather than as a stalled
spinner, and should be consistent about it run to run.

The 25 tokens per second figure is also useful as a concrete, published streaming rate for the Ask
screen, chosen by researchers holding it constant across conditions.

### 3.3 Skeleton screens are widely recommended and weakly evidenced

I looked for the study behind the common advice and it does not say what the advice implies. Mejtoft
et al. tested a fictional news site with skeleton screens against the same site with spinners. Skeleton
screens scored higher on average for both perceived speed and ease of navigation, but "the results of
this paper cannot show any significant differences in any of the comparisons between the web pages",
and users of the spinner version were actually *faster* at finding an article on first visit.
([Mejtoft, Långström, Söderström, ECCE '18](https://umu.diva-portal.org/smash/record.jsf?language=en&pid=diva2%3A1293450&dswid=-5542),
DOI [10.1145/3232078.3232086](https://doi.org/10.1145/3232078.3232086))

So: skeletons are not harmful, the direction of the effect is favourable, and the effect is not
statistically significant in the one controlled test I could find. Treat them as a cheap default for
*content* loading, not as a technique that has been shown to buy perceived speed. Do not cite them as
evidence-backed when justifying effort. For the job view specifically, there is a better option
anyway, which is showing the real step list from the first frame (section 4).

### 3.4 Animation pacing: the numbers are concrete and they argue for restraint

Material Design gives durations that are directly usable, and it draws an explicit desktop/mobile
distinction that most teams miss.

- Mobile transitions typically 300ms, with large full-screen transitions up to 375ms, elements
  entering at 225ms and leaving at 195ms.
- "Transitions that exceed 400ms may feel too slow."
- **Desktop animations "should last 150ms to 200ms"** and should be "faster and simpler than their
  mobile counterparts."
- The stated reason is candid: "Complex web transitions often result in dropped frames (unless they
  are built for GPU acceleration). Shorter durations will make these less noticeable because the
  animation completes faster."

([Material Design, Duration & easing](https://m1.material.io/motion/duration-easing.html))

Standard easing curves, with the CSS values, are also specified there: standard
`cubic-bezier(0.4, 0.0, 0.2, 1)`, deceleration `cubic-bezier(0.0, 0.0, 0.2, 1)`.

This app is a desktop-class interface, so the 150 to 200ms band applies, not the 300ms mobile figure.
It also follows that animation should never be load-bearing: a 30 minute job must not depend on motion
to communicate that it is alive, because motion that long is either imperceptible or annoying.

### 3.5 What is measurable, with targets

These are the numbers I would put on a dashboard. Where a target is a judgment rather than a cited
figure, I say so.

| Metric | Target | Basis |
|---|---|---|
| Response to a local UI action (selection, tab, expand) | under 100ms | Nielsen 0.1s instantaneous limit |
| Time to first frame of the job view after navigation, from cache | under 1s | Nielsen 1.0s flow-of-thought limit |
| First SSE byte after opening a job view | under 1s | applies the same limit to the stream |
| Heartbeat interval | 15s | WHATWG spec's "every 15 seconds or so", and `sse_starlette` `DEFAULT_PING_INTERVAL` |
| UI transition duration, desktop | 150 to 200ms | Material Design, desktop guidance |
| Any transition duration | under 400ms | Material Design, "may feel too slow" |
| Streaming rate on Ask (if used) | about 25 tokens/sec | CHI '26 held this constant across conditions |
| Reconnect to fully caught up after a drop | under 1s for a normal run | judgment call, not cited |

What is *not* on this list is a target for how long a job may take. That is a product decision about
the work, not about the interface, and the interface should not paper over it.

---

## 4. Showing the work without overwhelming a non-technical user

### 4.1 The debate is real and it does not resolve on its own

Luke Wroblewski, who has shipped several agentic interfaces, describes the split precisely: "some users
find the agent's work overwhelming and want the interface to focus purely on results. Others say
seeing that work is essential for monitoring and checking what the agent is doing. Strongly worded
feedback comes in from both sides." He expected it to fade as trust built, and reports that it has
not. ([LukeW, Showing the Work of Agents in UI](https://www.lukew.com/ff/2142/showing-the-work-of-agents-in-ui),
February 2026)

His reported progression is the most useful evidence in this report, because it is a record of
attempts that failed:

- At Bench, the team built multiple levels of progressive disclosure with tool calls collapsed by
  default. His verdict: "It was too much." Later iterations reduced visual weight and showed less
  process by default, and this "became even more important as the number of tools grew."
- At ChatDB they split the screen. While the agent works, the left column shows the decisions being
  made and the tools being picked. When results appear, that column "collapses down to a summary and
  link so the focus shifts to the output", and can be reopened.
- The design principle he draws out of it: "This approach allows the agent's work to serve as a
  detailed progress indicator, instead of forcing people to watch a spinner while things work."

That last sentence is the whole design for the Do screen. The step log is not a debugging view bolted
next to a spinner. It *is* the progress indicator.

### 4.2 Progressive disclosure has a hard limit of two levels

Nielsen's rules for progressive disclosure are more restrictive than they look. The benefits are
stated as improved learnability, efficiency of use, and error rate, and the mechanism is that "the
very fact that something appears on the initial display tells users that it's important."
([NN/g, Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/))

The constraint that applies directly to an agent trace: "In practice, designs that go beyond 2
disclosure levels typically have low usability because users often get lost when moving between the
levels."

So the job view should have two levels and no more. A summary line that expands to reveal the steps.
Not a summary, expanding to steps, expanding to raw tool arguments and model reasoning. Wroblewski's
account is an independent confirmation: his team went deep on disclosure levels and pulled back.

The design criterion Nielsen gives for choosing what belongs at level one is the one to apply: disclose
everything users frequently need up front, and do not put confusing items in the initial display,
because that slows performance.

### 4.3 Three failure modes, and what each one is

These are the ones worth designing against explicitly, because each has a different fix.

**The interface that looks frozen.** The connection is fine but nothing has happened for four minutes,
so a static screen is indistinguishable from a hung one. This is the one Nielsen's unknown-duration
advice addresses: report work done rather than a percentage. A timestamped line reading "Reading
pages 40 to 62" that updates is honest even when it updates slowly. Note that a 15 second heartbeat
keeps the *connection* alive but is invisible to the user by design, since comment lines are ignored,
so the heartbeat does not solve this problem. Only real events do.

**The interface that floods.** Nielsen names the failure: "flashing changes on the screen so rapidly
that the user cannot keep pace or feels stressed", which he calls a violation of display inertia.
Wroblewski's Bench experience is the same failure from the other direction, where too much process was
shown by default and "it was too much."

**The interface that lies.** This is the one that matters most here, and it is where the audit
constraint is an advantage rather than a burden. Because every event is an append-only row written in
the same transaction that assigns its sequence number, the UI has no ability to show something that
was not recorded. A progress indicator that is computed from intent rather than from the record would
be a lie. One computed from the log cannot be.

I looked for the strongest published version of this failure. A 2025 ACM paper studies "false front"
pages that render instantly and load content behind a facade, and finds a framework and reference
implementations demonstrating it is technically feasible
([Fake it 'til you load it, PACM HCI](https://dl.acm.org/doi/full/10.1145/3735593)). I could not
retrieve the full text through ACM's bot protection, so I am citing its existence and framing rather
than its measured results.

### 4.4 What to show, and what to keep until asked

This is my recommendation, derived from the sources above plus the audit constraint. It is a design
proposal rather than a cited finding, and I have marked it as such.

**Shown by default, at level one:**

- The named step currently running, in the client's language, not the tool's. "Checking the invoice
  against the purchase order", not `pdf_extract(page_range=40..62)`.
- The steps already completed, as a compact list with timestamps from the record itself.
- One honest indicator that time is passing, derived from real events.
- What happens at the approval gate, stated before it arrives, so the wait has a known shape.
- The stop control. Nielsen is explicit that anything slower than 10 seconds needs "a clearly
  signposted way for the user to interrupt the operation." The API already has `POST
  /jobs/{id}/cancel`.

**Kept at level two, one expand away:**

- The full ordered event list with sequence numbers. The Activity screen already has the machinery
  for this, and an auditor will want it.
- Per-step timing and retry counts, since the job model already records typed failures and retries.
- The exact documents and passages touched, which the citations model supports.

**Not shown at all:**

- Raw tool arguments and model reasoning. This is the "firehose of raw agent reasoning" the brief
  rules out, and Wroblewski's account is evidence that even technical users find it too much by
  default. It is also the thing most likely to be misread by a practice manager as the system being
  confused.
- Any percentage complete. The duration is not knowable in advance, and a percentage that moves
  backwards or sticks at 90 percent is worse than no percentage.

The line between levels should be drawn by consequence, not by technical depth. Something belongs at
level one if a person would need it to decide whether to trust the run or to stop it.

---

## 5. Approval gates that produce real decisions

### 5.1 The failure mode has hard numbers, and it is severe

The best data I found is from a study of code review, which is an adjacent domain but the closest
large-scale measurement of human approval behaviour available. Of all merged pull requests, **46% were
approved with no top-level or inline comment at all**, and **65% were approved with minimal comments**
(0 to 3 words across all reviews). The most common review text was a variation of "LGTM", followed by
the thumbs-up emoji, followed by "thank you". In 8.4% of merged PRs, multiple reviewers all approved
without leaving a single comment.
([Graphite, PRs merged after minimal review](https://www.graphite.com/research/prs-merged-after-minimal-review),
basis: pull requests merged by active committers from January 2023 onward)

That is the base rate for a population of professional engineers reviewing other engineers' work,
where the reviewer is technically capable and can be held responsible. A practice manager approving an
agent's work is in a weaker position on both counts. The design should assume that a presented
approval will often be a rubber stamp unless it is made genuinely easy to evaluate.

### 5.2 What reduces approval fatigue

These follow from the mechanism the numbers above imply. Where I am reasoning rather than citing, I
say so.

**Show what will happen, not what the agent concluded.** The decision at a gate is "may this proceed",
so the gate should state the specific action in concrete terms: which document, which value, which
recipient. An engineer approving a diff sees the diff. A practice manager should see the equivalent,
which is the proposed change itself rather than a summary of the agent's confidence.

**Show the evidence, and keep it next to the claim.** The Ask screen already established this pattern
with citations computed from the response. The approval gate should carry the same property: every
assertion the agent relies on should be one click from the passage that supports it. This is the single
most defensible thing this product can do, because the citations are already recorded.

**Prefer a bounded number of decisions.** Nielsen's disclosure rules say the initial display should
contain what users frequently need and nothing confusing. Applied to a queue, that argues for showing
the decision and its evidence, and keeping the run's full history one level down. A queue that shows
everything gets skimmed; a queue that shows the decision gets read.

**Make the record visible at the moment of approving.** The fact that the decision is being written to
a hash-chained audit log is the product's central claim, and it is also a behaviour lever. A person who
can see that their name and reasoning attach to a permanent record behaves differently from one
clicking a button. Whether that effect is real is something I could not find evidence for; I flag it as
a hypothesis worth testing rather than a finding.

### 5.3 Bulk approval, and the case against it

Bulk approval is where the rubber-stamping numbers get worse, and I would be cautious about shipping
it. The 8.4% figure above (multiple reviewers, zero comments) is the shape of the failure: approval
becomes a gesture applied to a batch rather than a judgement about an item.

If similar items genuinely need batch handling, the safer construction is to make the batch an
explicit object rather than a multi-select. The person approves a stated rule covering enumerated
items ("approve these 12 invoices, each under USD 500, all matching their purchase order"), and the
record captures the rule and the list. That is auditable in a way that twelve checkbox clicks are not,
and it forces the person to read the qualifying condition, because they have to state it.

The related design rule: never make the default action approve. If a person can clear a queue by
holding down a key, the queue will be cleared that way.

---

## 6. Resumability

### 6.1 The answer is easier here than in most systems, and the reason is the audit log

In a typical streaming design the connection is the only place a run's progress exists, so a dropped
connection loses information. Here it does not, because progress was never stored in the connection.
The event log is a table, and the stream is a view over it. That inverts the usual recovery problem.

### 6.2 The pattern to implement

The client should treat the stream as an optimisation over a polling fallback, never as the source of
truth. Concretely, on mounting the job view:

1. **Read state.** `GET /jobs/{job_id}` returns the job with its state and result, including the
   terminal states. Render a complete screen from this alone.
2. **Find the position.** Take the highest sequence number reflected in that response.
3. **Attach.** Open the stream with `?since=<seq>`, or with no parameter if replaying from the start
   is cheap.
4. **Reconcile on every event.** Update from events as they arrive.

After a network drop, a sleep, or a closed laptop, step 1 through 3 run again and the screen is correct
regardless of how much was missed. The specification supports this at the protocol level: after any
disconnect the browser reconnects and sends `Last-Event-ID`, so step 3 can be automatic once the
server honours that header ([WHATWG, section 9.2.3](https://html.spec.whatwg.org/multipage/server-sent-events.html)).

Four details that decide whether this works in practice:

- **Terminal states must stop the stream.** A screen that keeps a spinner on a finished job is lying
  about the one thing it exists to report. The job module already defines a `TERMINAL` set with
  exactly this reasoning in its comment, and the client should close the `EventSource` when it sees
  one. This also releases the per-host connection slot.
- **Replay must be idempotent on the client.** Because replay re-delivers events, the client should
  key its view on `seq` rather than appending blindly. Duplicate delivery is normal here, not an error.
- **The server needs a bound on replay size.** A long run could mean thousands of events, and a client
  asking for `?since=0` after a week should get a page and a resume point rather than the whole table.
  The `next_since` cursor already used in `services/api/activity.py` is the right precedent to copy.
- **`204 No Content` means stop.** If the server ever needs to tell clients not to reconnect, that is
  the specified signal ([WHATWG, section 9.2.1](https://html.spec.whatwg.org/multipage/server-sent-events.html)).
  Use it deliberately rather than closing the socket and letting the client retry forever.

### 6.3 Does the audit constraint change the answer?

It sharpens it in one direction. Because the client can trust the record to be complete, it never needs
to reconstruct state from a partial stream or ask the server to resend anything it cannot name. The
sequence number is a cursor into durable storage, which is exactly the shape a resumable protocol
wants.

The constraint adds one requirement. If the UI only ever shows what was recorded, then an interrupted
run must be shown as interrupted rather than as still working. A job whose worker died mid-step has a
record that says so, and the screen should not paper over the gap with an animation.

---

## 7. The principles, collected

For the phase that adds agentic work and the Review screen.

**Transport**

1. Keep SSE on `GET /jobs/{job_id}/events`. Do not add WebSockets.
2. Emit `id: <seq>` on every frame so browser reconnect carries `Last-Event-ID`.
3. Honour `Last-Event-ID` and `?since=`, and document which takes precedence.
4. Pin `ping=15` explicitly on the jobs endpoint rather than relying on the library default.
5. Keep `X-Accel-Buffering: no` and `Cache-Control: no-store`, and note that a proxy can override the
   former via `proxy_ignore_headers`.
6. Keep polling as a documented degraded mode against the same `?since=` endpoint.

**Showing the work**

7. The step log is the progress indicator. Do not put a spinner next to it.
8. Two disclosure levels, never three.
9. Level one: the current step in the client's language, completed steps with timestamps, what happens
   at the gate, and the stop control.
10. Level two: the full event list with sequence numbers, timings, retries, and source passages.
11. Never show a percentage complete.
12. Never show raw tool arguments or model reasoning by default.
13. Never show anything that is not in the record.
14. Treat a long, well-explained wait as deliberation, and be consistent about it run to run.
15. Desktop transitions at 150 to 200ms, never above 400ms.

**Approval**

16. State the specific action, not the agent's confidence.
17. Keep the supporting evidence one click from each claim.
18. Make the audit record visible at the moment of approval.
19. Never make approve the default action.
20. Implement bulk approval as an explicit stated rule over enumerated items, not as multi-select.

**Resumability**

21. Render a complete screen from `GET /jobs/{id}` before attaching the stream.
22. Resume with `?since=`, key the view on `seq`, and tolerate duplicate delivery.
23. Close the stream on a terminal state.
24. Bound replay size and return a resume cursor.
25. Show an interrupted run as interrupted.

---

## 8. What I could not verify

Listed so a later reader does not mistake silence for confirmation.

- **Cloudflare's exact stream timeout.** The roughly 100 second figure comes from a third-party
  knowledge base, not Cloudflare documentation, which I could not retrieve. It does not change the
  design because the heartbeat is 15 seconds.
- **The full text of the "false front" web pages paper** ([DOI 10.1145/3735593](https://dl.acm.org/doi/full/10.1145/3735593)).
  ACM's bot protection blocked retrieval. I cite its framing, not its measured results.
- **Whether showing the audit record at approval time changes approval behaviour.** I found no study.
  Presented as a hypothesis, not a finding.
- **How often corporate TLS-inspecting proxies break long-lived HTTP streams.** No measurement found.
  The argument for SSE over WebSockets on this point is structural, not empirical.
- **Whether the CHI '26 latency finding transfers from chat answers to monitored jobs.** The study
  covers a chat interaction. Applying it to a job view is my inference.
- **Published end-to-end benchmarks of SSE versus WebSockets at this scale.** I found no neutral
  benchmark. The recommendation rests on protocol fit, proxy behaviour, and operational surface
  rather than on a throughput comparison, because throughput is not the binding constraint here.

---

## Sources

Primary specifications and vendor documentation:

- [WHATWG HTML Standard, section 9.2 Server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- [MDN, EventSource](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)
- [nginx, ngx_http_proxy_module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
- [AWS, Edit attributes for your Application Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-load-balancer-attributes.html)
- [sse_starlette source, `sse.py`](https://raw.githubusercontent.com/sysid/sse-starlette/main/sse_starlette/sse.py)

Research:

- [Tan, Messerschmidt, Yin, Nov. The Impact of Response Latency and Task Type on Human-LLM Interaction and Perception. CHI '26.](https://ar5iv.labs.arxiv.org/html/2604.06183) DOI 10.1145/3772318.3790716
- [Mejtoft, Långström, Söderström. The effect of skeleton screens. ECCE '18.](https://umu.diva-portal.org/smash/record.jsf?language=en&pid=diva2%3A1293450&dswid=-5542) DOI 10.1145/3232078.3232086
- [Fake it 'til you load it: User Perceptions and Performance with Fast-Loading "False Front" Web Pages. PACM HCI.](https://dl.acm.org/doi/full/10.1145/3735593)

Practitioner and reference:

- [Nielsen, Response Times: The 3 Important Limits. NN/g.](https://www.nngroup.com/articles/response-times-3-important-limits/)
- [Nielsen, Progressive Disclosure. NN/g.](https://www.nngroup.com/articles/progressive-disclosure/)
- [Wroblewski, Showing the Work of Agents in UI. February 2026.](https://www.lukew.com/ff/2142/showing-the-work-of-agents-in-ui)
- [Material Design, Duration & easing.](https://m1.material.io/motion/duration-easing.html)
- [Graphite, 65% of merged PRs are approved after minimal review.](https://www.graphite.com/research/prs-merged-after-minimal-review)

Unverified, cited only for completeness:

- [Stackharbor, WebSockets and SSE through Cloudflare](https://stackharbor.com/en/knowledge-base/cffix-websockets-sse-behind-cloudflare/)
