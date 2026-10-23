#!/usr/bin/env python3
"""Rotate the client connector token without rebuilding the gateway or the connector.

The secret is read from AWS Secrets Manager. The gateway reads its credential file on every call, so the
gateway needs no restart. The connector keeps its token in an environment file, so it is restarted through
systemd after the file changes. That is a service restart, not a redeploy.

Usage:
    rotate_connector_secret.py --secret-id oi-core/connector-token \
        --token-file /home/ubuntu/client/connector.token \
        --env-file /home/ubuntu/client/connector.env --service client-connector
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import boto3


def rotate(secret_id: str, token_file: Path, env_file: Path, service: str) -> None:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    value = boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_id)["SecretString"].strip()
    if not value or "\n" in value:
        raise SystemExit("the secret is empty or contains a newline")

    token_file.write_text(value)
    os.chmod(token_file, 0o600)

    lines = []
    if env_file.exists():
        lines = [line for line in env_file.read_text().splitlines() if not line.startswith("CONNECTOR_TOKEN=")]
    lines.append("CONNECTOR_TOKEN=" + value)
    env_file.write_text("\n".join(lines) + "\n")
    os.chmod(env_file, 0o600)

    subprocess.run(["sudo", "systemctl", "restart", service], check=True)
    print(f"rotated {token_file} and restarted {service}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="rotate a connector token from Secrets Manager")
    parser.add_argument("--secret-id", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--service", required=True)
    args = parser.parse_args(argv)
    rotate(args.secret_id, args.token_file, args.env_file, args.service)
    return 0


if __name__ == "__main__":
    sys.exit(main())
