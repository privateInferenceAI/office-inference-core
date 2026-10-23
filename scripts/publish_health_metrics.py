#!/usr/bin/env python3
"""Publish the five health metrics the alarms watch.

Runs on the build box as the ubuntu user. It uses the instance role for CloudWatch and Docker for the service
checks. A metric that cannot be measured is published as a failing value, not omitted, so missing data pages.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import boto3

NAMESPACE = "OI/Health"
DEPLOYMENT = "oi-core-dev-cloud-workspace"
ROOT = Path("/opt/oi-core")
BACKUP = Path("/var/backups/oi-core/backup.json")
AUDIT_MARKER = Path("/var/lib/oi-health/audit-chain.json")


def run(command: list[str], timeout: int = 20) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - a failed probe is a failed probe
        return ""


def api_healthy() -> bool:
    code = ("import urllib.request; "
            "print(urllib.request.urlopen('http://127.0.0.1:8088/health', timeout=5).status)")
    return run(["docker", "exec", "api", "python", "-c", code]) == "200"


def postgres_healthy() -> bool:
    return "accepting connections" in run(
        ["docker", "exec", "postgres", "pg_isready", "-U", "oicore", "-d", "oicore"])


def litellm_healthy() -> bool:
    code = ("import urllib.request; "
            "print(urllib.request.urlopen('http://litellm:4000/health/liveliness', timeout=5).status)")
    return run(["docker", "exec", "api", "python", "-c", code]) == "200"


def ingestion_healthy() -> bool:
    return "healthy" in run(
        ["docker", "inspect", "ingestion", "--format", "{{.State.Health.Status}}"])


def backup_age_hours() -> float:
    if not BACKUP.exists():
        return 9999.0
    try:
        payload = json.loads(BACKUP.read_text())
        stamp = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds() / 3600)
    except Exception:  # noqa: BLE001 - an unreadable backup is a failed backup
        return 9999.0


def audit_chain_valid() -> bool:
    try:
        return bool(json.loads(AUDIT_MARKER.read_text()).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def disk_percent() -> float:
    usage = shutil.disk_usage("/")
    return round(100.0 * usage.used / usage.total, 2)


def main() -> int:
    checks = {
        "api_ok": api_healthy(),
        "postgres_ok": postgres_healthy(),
        "litellm_ok": litellm_healthy(),
        "ingestion_ok": ingestion_healthy(),
        "backup_age_hours": backup_age_hours(),
        "audit_chain_ok": audit_chain_valid(),
        "root_disk_percent": disk_percent(),
    }
    core = checks["api_ok"] and checks["postgres_ok"] and checks["litellm_ok"]
    values = [
        ("CoreHealthy", 1.0 if core else 0.0, "Count"),
        ("IngestionHealthy", 1.0 if checks["ingestion_ok"] else 0.0, "Count"),
        ("BackupAgeHours", checks["backup_age_hours"], "Count"),
        ("AuditChainValid", 1.0 if checks["audit_chain_ok"] else 0.0, "Count"),
        ("RootDiskUsedPercent", checks["root_disk_percent"], "Percent"),
    ]
    boto3.client("cloudwatch", region_name="us-east-1").put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[{"MetricName": name, "Value": float(value), "Unit": unit,
                     "Dimensions": [{"Name": "Deployment", "Value": DEPLOYMENT}]}
                    for name, value, unit in values])
    print(json.dumps({"checks": checks, "core": core}, sort_keys=True))
    return 0 if core else 1


if __name__ == "__main__":
    raise SystemExit(main())
