# 0005. Measurement hygiene: one suite at a time, and every run records its context

Date: 2026-10-20
Status: accepted

## Context

On 2026-10-20 the same configuration, with no flag or image changed, measured 53 of 53 five times in a window when
the development box was otherwise idle, then 48 to 51 of 53 in a window when a second suite was running against the
same deployment. N06 and N07, the two anchor-number table questions, failed in the lower runs and passed in the
higher ones. `docs/reviews/exact-phrase-2026-10-20/README.md` records the 48 and 49 controls against the 53 of 53
measured earlier the same day.

The run files recorded no commit, no answer flags, no image identity, no load and no memory. After the fact, the
two windows could not be separated from the result, so every number in them was quotable and none was defensible.
The suite script took no lock, checked no process and wrote no provenance, and the practice lived only in prose.

That is the failure this record exists to stop. A measurement taken under contention is not repaired by a caveat
written next to it.

## Decisions

1. **One suite at a time per box.** A second `scripts/run_answer_suite.sh`, a manual
   `tests/fixtures/data_suite_run.py`, or any `docker compose run` against the same deployment while a suite is
   active disqualifies both runs. Not both runs with a note. Both runs.
2. **The host script enforces it with flock.** `scripts/run_answer_suite.sh` opens `/tmp/oi-suite.lock`
   (`OI_SUITE_LOCK` overrides the path) on descriptor 9, takes a non-blocking exclusive lock before it reads the
   deployment, and holds it for the script's life. If another suite holds it, the script exits 3 with a sentence
   naming the lock and the holder's pid. The lock is released by the kernel on any exit.
3. **The runner enforces it in Postgres.** `tests/fixtures/data_suite_run.py` takes
   `pg_try_advisory_lock(20261020)` on the database it already reads, over its own connection, and releases it when
   the run ends. A busy lock exits 4 with a sentence, not a traceback. This is the lock that catches a direct
   `docker run` of the runner, which never passes through the script. The key is fixed and must stay fixed:
   changing it would silently stop excluding older runners that still carry it.
4. **Load and memory are admission conditions.** Before the first container, the script reads `/proc/loadavg` and
   `MemAvailable` from `/proc/meminfo` and refuses to start above `OI_SUITE_MAX_LOAD` (default 2.0) or below
   `OI_SUITE_MIN_AVAILABLE_MB` (default 2048). The default load is calibrated for the 16-vCPU reference box, where
   the suite alone sits far below it. A missing signal is a refusal, a mistyped limit is an error rather than a
   fallback, and a refused run starts no container and writes no context. Raising either limit is a decision to
   measure under contention, and belongs in this record.
5. **Every run records the context it ran in.** Before the first run, the script writes `run-context.json` into the
   output directory with the timestamp, the git commit, the `OI_*` variables the API container runs with (minus
   credentials and the runner's own plumbing), the `api` and `eval` image ids and digests where Docker can name
   them, the one-minute load, `MemAvailable`, the hostname and the instance id when one is readable. Each run's
   JSON carries the same record under `context`, because the runner reads the script's file instead of rebuilding
   the facts inside the container.
6. **The panel warns, and does not fail old data.** `scripts/acceptance_panel.py` adds a `warnings` list to the
   panel and prints each warning to stderr when a run has no context, has no load average in its context, or
   started above the load limit recorded with it. Run files written before this decision have no context at all
   and still score. They are the evidence behind the reviews, and refusing them would discard the record instead
   of annotating it.
7. **The existing measurement rules stay.** Five runs minimum, a same-day control, a frozen corpus, model and
   flag set, and the raw JSON kept. A control that moves by more than the five-run spread is a stop signal, not a
   data point. This record adds the serial rule beside them, it does not replace them.

8. **One provider account is one measurement resource.** The host and database locks only see one machine. Both
   deployments here share the same AWS account and region, so they share the Bedrock per-day token quota. On
   2026-10-20 the acceptance run for PR #211 started a suite on each box at once; the quota ran out mid-series and
   the provider began returning 429 "Too many tokens per day", which turned the remaining runs into degraded
   answers (corpus 1 run 4 scored 24 of 53, corpus 2 runs 2 to 5 scored 15 to 22 of 37). Nothing about the code
   changed; the runs are invalid and were discarded. Run one suite at a time across every deployment that shares
   a provider account, and treat a mid-series 429 as a stop signal: the series must be re-run, not averaged.

## Consequences

- `scripts/run_answer_suite.sh` exits 3 when another suite holds the host lock, and 4 when the gate refuses or
  the context cannot be written. `tests/fixtures/data_suite_run.py` exits 4 when the advisory lock is busy or
  Postgres cannot be reached.
- A direct runner gets a thinner context. The eval image has no git, so the commit is null, and image identities
  are absent. Run through the script for a complete record; the panel still warns about what is missing.
- The load gate covers runs started through the script. A direct runner is serialized by the database lock but not
  gated on load, and its recorded load is what lets the panel warn afterwards.
- **No ingestion cycle during a run, and no deployment change during a run.** The suite has no reliable signal for
  either, so these stay operator rules and this record does not pretend otherwise.
- The first measurement taken under these rules is the corpus-1 live control that the exact-phrase PR left open,
  re-run in a window that the script admits.
- Provider quota is not enforceable from the box. A suite on another machine sharing the account can exhaust the
  day's tokens mid-series; the run context records the region and model but not the remaining quota, so the
  operator keeps this rule by hand and the 429 in the logs is the signal that a series is dead.
