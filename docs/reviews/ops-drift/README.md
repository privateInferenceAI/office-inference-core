# Deployment state is reportable and drift is detectable

**Date:** 2026-10-23. **Status:** OPS-DRIFT-001 is verified.

## What is reported

`scripts/deployment_state.py` reads the compose file and the rendered manifest and writes:

```json
{"manifest_sha256": "...", "images": {"litellm": "oi-core/litellm:local", "...": "..."}}
```

The expected copy is kept with the release. A pin changed on the box but not in the release shows as a
different image reference.

## Evidence

- Expected state generated from the repository compose file.
- The litellm image on the box changed from `oi-core/litellm:local` to `oi-core/litellm:drifted`.
- `--compare` reported `litellm: expected 'oi-core/litellm:local', found 'oi-core/litellm:drifted'` and exited 1.
- Restoring the compose file made `--compare` report `no drift` and exit 0.

## Tests

`tests/test_deployment_state.py`: 2 passing, one for a changed image and one for unchanged pins.

## Files

- `scripts/deployment_state.py`.
- `tests/test_deployment_state.py`.
- `drift.txt`: the two compare outputs.
