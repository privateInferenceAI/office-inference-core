# Image and dependency scanning

**Date:** 2026-10-23. **Status:** OPS-CVE-001 is verified.

## What runs

`scripts/scan_images.sh` scans every deployment image with Trivy and writes one JSON report per image plus a
summary. A systemd timer runs it weekly on the box:

```
oi-image-scan.timer  next Mon 2026-10-26 00:00 UTC  weekly
```

## Current report

The scan covered 13 deployment images. Summary:

| Image | Critical | High | Total |
|---|---:|---:|---:|
| oi-core/api | 0 | 44 | 170 |
| oi-core/litellm | 1 | 4 | 35 |
| oi-core/parser | 5 | 38 | 83 |
| postgres:16-alpine | 1 | 25 | 61 |
| qdrant/qdrant | 0 | 45 | 166 |
| text-embeddings-inference | 0 | 16 | 366 |
| oi-core/mcp-gateway | 0 | 44 | 170 |

The full per-image numbers are in `summary.json`. Most of the API-image findings are in the Debian base, not
in application code; the critical findings in litellm, parser and postgres are real and are not waived here.
They are now visible and scheduled; triage and upgrade work belongs to the next security pass.

## LiteLLM pin and version

The compose file records `# LiteLLM v1.104.2, pinned 2026-10-10` beside the image. The running container
reports `1.104.2` through `importlib.metadata.version("litellm")`.

## Files

- `summary.json`: the scan summary.
- `timer.txt`: the scheduled timer output.
- `litellm-version.txt`.
