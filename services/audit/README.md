# audit

The record's only writer, and the tool that checks it.

Everything it does is a subcommand: `migrate`, `record`, `list`, `backfill`, `export`, `verify`,
`anchor` and `purge`. Run it through compose, which is what gives it a database and a mounted
exports directory:

    docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env \
      run --rm audit list --type model_call --limit 5

The operational detail, including what each subcommand prints and what it refuses, is in
`docs/manual.md` section 13. The design of the record itself, and what each check does and does not
prove, is in `docs/audit-evidence.md`.

Two things worth knowing before changing it:

- The row hash is computed by a database trigger **and** by `row_canonical()` here, which have to
  agree field for field. Changing one without the other makes every export fail verification.
- Refusals are recorded as well as calls, classified from the gateway's spend log. `AUD-HASH-001` in
  the register records which columns the hash does not cover.
