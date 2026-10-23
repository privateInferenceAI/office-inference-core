#!/usr/bin/env python3
"""Export and verify the audit chain, and leave the result where the metric publisher reads it.

Runs on the build box as the ubuntu user. The API container owns the audit tool and the database URL.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

MARKER = Path("/var/lib/oi-health/audit-chain.json")
OUT = "/tmp/oi-audit-check"


def main() -> int:
    export = subprocess.run(
        ["docker", "exec", "api", "python", "/app/audit.py", "export", "--out", OUT],
        capture_output=True, text=True, timeout=120)
    verify = subprocess.run(
        ["docker", "exec", "api", "python", "/app/audit.py", "verify",
         OUT + "/audit-events.jsonl"],
        capture_output=True, text=True, timeout=120)
    ok = export.returncode == 0 and verify.returncode == 0
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps({
        "ok": ok,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "export_rc": export.returncode,
        "verify_rc": verify.returncode,
        "verify_output": verify.stdout.strip()[:500],
        "verify_error": verify.stderr.strip()[:500],
    }, indent=2) + "\n")
    print(MARKER.read_text())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
