# Critical CVE patch, 2026-10-23

**Status:** the three critical image findings are patched and rescan clean. High findings remain and are not
claimed fixed.

## Before

| Image | Critical finding | Package | Fixed |
|---|---|---|---|
| postgres:16-alpine | CVE-2025-68121 | gosu stdlib Go 1.24.6 | Go 1.24.13, 1.25.7 |
| oi-core/litellm | GHSA-6q48-6c7w-9gw2 | certifi 2026.4.22 | 2026.10.19 |
| oi-core/parser | GHSA-6q48-6c7w-9gw2 | certifi 2026.7.22 | 2026.10.19 |
| oi-core/parser | CVE-2026-112393, CVE-2026-112403, CVE-2026-112900, CVE-2026-96639 | Pillow 12.3.0 | 13.0.0 |

## What changed

- Postgres is now oi-core/postgres:local, built from postgres:16-alpine with gosu compiled from source by the
  patched Go 1.25 toolchain. The upstream image and Alpine's prebuilt gosu package both still carried the
  vulnerable Go runtime.
- LiteLLM installs certifi 2026.10.23. pip is removed after the install because its vendored certifi was the
  stale package Trivy kept reporting.
- Parser installs certifi 2026.10.23 and Pillow 13.0.0. pip is removed after the install for the same reason.

## After

- oi-core/postgres:local 038eb7027973: zero criticals.
- oi-core/litellm:local 327b233adf02: zero criticals.
- oi-core/parser:local c836229623b1: zero criticals.

The full high-severity totals are not zero. They are not waived; the next security pass owns them.

## Verification

- Postgres: 16.15 running, API health ok, audit export and verify intact after the switch.
- LiteLLM: health/liveliness returned 200 and the API answered after the recreate.
- Parser: health ok, Pillow 13.0.0 imports, container healthy.
- Targeted Trivy image scans were run against each patched image; the critical list is empty.

## Rollback

Pre-patch tags are on the box: oi-core/postgres:pre-cve-2026-10-23b, oi-core/litellm:pre-cve-2026-10-23b,
oi-core/parser:pre-cve-2026-10-23b. The pre-patch backup is
s3://oi-core-backups-878451098165/backups/20261023T135902Z/.
