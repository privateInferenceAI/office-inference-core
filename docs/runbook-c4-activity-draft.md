### 5.1b The activity read API, so the record can be read without a shell

**Goal.** A client can read the audit chain over the API, and cannot read a row outside its own permission.

**Why this is an endpoint rather than "use the audit CLI".** The chain is the product's central claim, and
until now the only way to read it was `audit list` on the box. That is an operator tool behind an SSH
session, not an interface, and the client app lives in its own repository and reaches this service over HTTP
and nothing else. **Activity is the screen that makes the product auditable to a client**, and it had no
endpoint at all.

**The scoping rule, which is the part worth arguing about.** The audit table has no per-row permission
column, and inventing one would be a second permission model maintained beside the real one. So the rule
reads from the model that already exists:

| Who | What they see |
|---|---|
| any session | its own rows |
| a session whose roots include another subject's roots | that subject's rows too |

**The direction matters and it is the safe one.** A session with `company` sees itself and every other
`company`-scoped subject. A session with `company` and `executive` sees everyone. **Nobody sees a row whose
actor holds a root they do not hold**, so the executive rows stay invisible to a company-scoped role, which
is the same boundary `SEC-ACL-001` enforces on retrieval. The stored `access_roots` on each session is a
snapshot, so the comparison is against the permission in force when the row was written rather than the
permission today.

**A subject with no session record is not visible to a narrower role.** The rule is a subset test, not a
guess, and an unknown actor falls to the narrower side.

**What a row carries.** The record's own columns, and the payload, because an activity screen that cannot
show the request, the model, the documents read and whether content left is a table of timestamps. **The
payload is already the content-mode-governed form**: `content.classes` decides whether a prompt is stored as
a hash, redacted, or in full, and this endpoint reads what was stored rather than deciding for itself. It
does not widen what a row contains.

### The endpoint

| Method | Path | What it does |
|---|---|---|
| GET | `/activity` | the chain, newest first, filtered and scoped to the session |

| Parameter | What it filters |
|---|---|
| `limit` | how many rows, default 50, capped at 500 |
| `since` | rows after this id, for paging and for catching up |
| `type` | `model_call`, `tool_call`, `approval`, `access`, `config_change`, `login` |
| `actor` | one subject, if the session may see it |
| `outcome` | `ok`, `denied`, `error`, `pending` |
| `job` | the `job_id` a row names, which is how a job's whole history is read back |
| `request` | the `request_id` an answer carries, which is how the screen ties an answer to its row |

**A `404` is not used for a row outside the scope.** The filter simply does not match it, so a caller cannot
learn that a row exists by asking for it and reading the difference between "no" and "not there". That is the
same position the 403 wording already takes.

### Run it

```
cd /opt/oi-core
docker run --rm --network ai-net --user "$(id -u):$(id -g)" \
  -v /opt/oi-core:/w -w /w --env-file rendered/env \
  --entrypoint python oi-core/session:local /w/tests/fixtures/activity_probe.py
```

Expected:

```
ok   READ       12 row(s) returned, newest first, ids descending
ok   SCOPED     a company-scoped session cannot see an executive subject's row
ok   FILTERED   type=model_call returns only model calls; outcome=denied only denials
ok   BYREQUEST  the row for a request_id is returned by asking for that request_id
ok   BYJOB      a job's rows are all returned by asking for that job
ok   CAPPED     limit=500 is the ceiling and a larger number is refused rather than honoured
PASSED: 6 right(s) held against the documented activity interface
```

**If it breaks.**

- **An empty list on a deployment that has rows**: the session's deployment id does not match, or the actor
  comparison is failing because the session's roots snapshot is empty.
- **Rows from another deployment**: the query must filter on `deployment_id`. Two deployments can share a
  database, and a chain that mixes them is not evidence about either.
- **A row with no payload**: the payload column is nullable and older rows may not have one. That is not an
  error, and a screen should render the columns it has.
- **`limit` above the cap silently honoured**: the cap exists so one request cannot pull the whole chain.
  Refusing rather than trimming is deliberate: a caller that asked for 5,000 and got 500 has been lied to
  about what it read.
