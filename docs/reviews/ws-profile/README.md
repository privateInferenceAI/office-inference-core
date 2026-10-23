# Cloud-only workspace profile, first slice

**Date:** 2026-10-23. **Status:** WS-PROFILE-001 is verified by the validator and its test.

## What changed

- The `workspace_brief` definition moved out of the Agentic Desk pack into its own `workspace` pack.
- The pack declares the services an office deployment does not run: `workspace-host`, `egress-proxy` and
  `artifact-store`.
- The cloud development manifest includes `core`, `document-chat`, `agentic-desk` and `workspace`.
- The validator refuses the workspace pack on any profile other than `cloud`.
- `tests/test_validate_manifests.py` covers the office refusal.

## Evidence

```
$ python3 scripts/validate_manifests.py
10/10 manifests valid; 9 packs known

$ python3 -m pytest tests/test_validate_manifests.py::test_the_workspace_pack_is_refused_on_an_office_profile
1 passed
```

The shipped-manifest test also renders the cloud workspace manifest with the new pack, so the pack is not only
refused in the wrong place; it is accepted in the right one.

## What remains

The real deployment still runs the workspace services on the same box. A separate workspace host, its security
group and its IAM role are the infrastructure part of the profile.
