# The psycopg stub collision, diagnosed

**Found by `panel-executor`.** Two groups of tests disagree about `psycopg` and they cannot both be satisfied
by the current arrangement.

## The shape

`tests/test_audit_chain.py`, `test_audit_record.py`, `test_audit_purge.py`, `test_audit_verify.py`,
`test_retention.py` and `test_activity.py` each install a fake module:

    if "psycopg" not in sys.modules:
        psycopg = types.ModuleType("psycopg")
        psycopg.types.json.Jsonb = lambda value: value
        sys.modules["psycopg"] = psycopg
        ...

**The guard asks whether the driver has been imported, not whether it is installed.** pytest imports every
test module before running any test, so whichever audit file loads first installs the stub, and the real
`import psycopg` in `test_store_parity.py` and `test_workflow_chain.py` then finds the stub already in
`sys.modules` and takes it. The result is `ModuleNotFoundError: No module named 'psycopg.types';
'psycopg.types' is not a package` — nineteen errors from a driver that is installed and working, while each
file run alone passes.

## Why the obvious fix is wrong

Changing the guard to a real `try: import psycopg.types.json` **breaks the audit tests instead**, and I tried
it and reverted it. With the real driver, `Jsonb` wraps its value, so `payload["prompt"]` raises
`TypeError: 'Jsonb' object is not subscriptable` at `test_audit_record.py:189` and `:197`. Those tests were
written against the stub's identity function and never unwrap.

    4 failed, 894 passed, 20 skipped     <- the try-import version, reverted
    896 passed, 22 skipped               <- as it stands, with the stub

## What the fix actually has to do

**The two groups have opposite needs and the collision is inherent.** The audit tests want `Jsonb` to be a
passthrough because they never touch a database. The DB-backed tests want the real driver. One pytest session,
one `sys.modules`.

Three ways, in the order I would try them:

1. **Make the audit tests work with either.** Unwrap where they read a stored payload — `payload["prompt"]`
   becomes something that handles a `Jsonb` wrapper. That is the honest fix: a test that only works against a
   fake is a test of the fake. The cost is touching the assertions in five files.
2. **Move the DB-backed tests behind a marker and run them in their own session.** Cheapest, and it matches
   what the pages already imply — `test_workflow_chain.py` skips without `OI_TEST_DATABASE_URL`. The cost is a
   suite that no longer runs everything in one command, which is the command the pages document.
3. **Have a `conftest.py` import the real driver before any stub can install.** Smallest, and the most
   fragile: it makes the outcome depend on import order, which is what went wrong in the first place.

**Option 1 is right and option 2 is defensible.** I did not have the context left to do either properly, and
leaving the suite green with a known collision is better than leaving it red with a half-fix.
