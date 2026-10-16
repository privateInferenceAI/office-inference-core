# intake

**Reads one mailbox the client owns and turns what is in it into documents with provenance.**

An invoice arrives at `ap@client.com`. This service reads it with a delegated `Mail.Read` credential the
client granted, hashes the attachment, writes the bytes under the documents root, and records who sent it,
which message it came from, when it arrived and the sha256 of the content. It approves nothing, sends nothing,
and reaches nothing but that one mailbox.

## Why it is shaped this way

Three findings decide the design, and all three are in
`docs/research/email-intake/handover-owned-email-intake.md`.

**A shared mailbox cannot be used at all.** Microsoft keeps sign-in blocked on the associated account and its
password is system-generated, while delegated OAuth needs one interactive sign-in to mint a refresh token. A
shared mailbox therefore forces application permission, and application `Mail.Read` reads every mailbox in the
tenant. The client needs one licensed regular mailbox, and that is a purchase rather than a setting.

**Delegated permission is the only scope that is right without PowerShell.** It reads the signed-in user's
mail and needs no Exchange RBAC grant. Application permission is the one that needs scoping, and its scope is
*additive to the Entra grant*, so an administrator who completes three of four steps builds something that
looks scoped and is not.

**The app registration cannot be created anywhere but the client's tenant.** There is no supported move of one
between tenants, and a service principal in their tenant pointing at an app object in ours is a design the
client cannot administer.

## The controls, and where each one lives

| Control | Where |
|---|---|
| Nothing resolves to a host we control | `net.py`, checked on every request before a socket is opened |
| The mail API is read-only | `net.py`, which refuses any Graph method but `GET` |
| One permission, refused by name | `config.py`, which refuses `Mail.ReadWrite`, `Mail.Send` and application permissions |
| The credential is read at the point of use | `secretstore.py`, which caches nothing and refuses a file another account can read |
| A silent failure is noticed | `monitor.py`, a heartbeat plus a volume floor |
| The folder connector polls | `folder.py`, which holds no filesystem watch at all |
| An instruction in a body reaches no action | `content.py`, which treats a body as data and has no action to request |

## Commands

```
python3 intake.py check-config    --config <client.yaml>     what the configuration must satisfy
python3 intake.py cord-check      --config <client.yaml>     cut every cord and see what is left
python3 intake.py migrate         --dsn "$DATABASE_URL"      intake's own table
python3 intake.py fetch           --config <client.yaml>     one poll
python3 intake.py watch           --config <client.yaml>     the same, on an interval
python3 intake.py watch-folder    --config <client.yaml>     the second channel, polled
python3 intake.py enumerate-reach --config <client.yaml>     what the credential can reach
python3 intake.py monitor         --config <client.yaml>     the heartbeat and the volume floor
python3 intake.py verify-manifest --config <client.yaml> --dsn "$DATABASE_URL"
python3 intake.py status          --config <client.yaml>     what is in the record
```

Exit codes are part of the interface, because a timer is the usual caller. 0 is success, 1 is a refused
configuration or a failed check, and 2 is a mail failure the heartbeat has already recorded.

## Where the record goes

`<state_dir>/intake-events.jsonl` is the record of record: append-only, on the client's own disk, readable
with no driver. The Postgres mirror exists so the row can be joined to the rest of the system, and
`schema/001_intake_events.sql` is applied by `migrate`. The schema file sits beside the service rather than in
`db/migrations/` because that directory is owned by another writer this phase; moving it there later is a
rename.

One file per deposited document lands in `<state_dir>/outbox/`, and that is the whole seam to the workflow. A
duplicate writes no notice, which is how "the workflow did not run twice" is a fact about the filesystem
rather than a claim about the workflow.

## Registration

The service is written and tested. Three registrations are **not done**, because the files are outside this
piece's write scope and belong to whoever owns the deployment:

1. `docs/service-inventory.md`, the list of what exists and which build it runs in.
2. `packs/catalog.yaml`, in the pack that should turn it on.
3. `deploy/office/compose/docker-compose.yml`, with the `tools` profile, the config file mounted read-only,
   and the client's secret store mounted at the path in `secret_store.path` and nowhere else.

`service.yaml` is written and is what the renderer reads.

## Tests

`tests/test_intake_*.py` cover the ten register rows this piece owns, and
`tests/fixtures/intake_acceptance.py` proves them in one run without a database or a tenant. Manual section
13l.7 is the procedure; runbook step 6.4 carries the output.
