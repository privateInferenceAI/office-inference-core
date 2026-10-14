# The custom_auth spike

Three files that exist to record what was run on 2026-10-14, and to be deleted once the real hook has tests.

- `custom_auth.py` is a LiteLLM `custom_auth` hook: an HMAC over a role, plus the master-key exception that
  keeps administration working.
- `Dockerfile` wraps the pinned gateway digest so the behaviour under test is the version we run.
- `config.yaml` is the spike's gateway config: one model, `custom_auth`, and the two common-check flags.

**It is not a service and it does not ship.** It was built and run on the development box against a throwaway
gateway sharing the deployment's database, and the findings are recorded in
`docs/design/c3-assistant-api-decisions.md` section 7a.

**The one thing worth reading before deleting this.** With `custom_auth` enabled the master key passes through
the hook like any other credential. A hook that only resolves role assertions therefore refuses the master key,
and every management endpoint becomes unreachable. The hook receives `api_key`, so the fix is to compare it
against `LITELLM_MASTER_KEY` and return a `proxy_admin` identity. Without that line, enabling this feature locks
a deployment out of its own budget administration.
