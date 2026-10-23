#!/usr/bin/env python3
"""Build, sign, verify, install and roll back a deployment release.

The manifest is canonical JSON over the git commit, the compose file and the image ids. It is signed with the
KMS asymmetric key alias/oi-key through the oi-release-signer role. No private key exists on this box.

The install path records the images it replaces in rollback.json before changing anything, then retags the
release images to the local tags the compose file uses. A failed health check automatically restores the
recorded images.

This is deliberately a state-level release, not a fresh container image registry. A real client release should
also publish image digests and preserve the release directory off the box.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

ROLE = "arn:aws:iam::878451098165:role/oi-release-signer"
KEY = "alias/oi-key"
ALGORITHM = "RSASSA_PKCS1_V1_5_SHA_256"
ROOT = Path(os.environ.get("OI_ROOT", "/opt/oi-core"))
COMPOSE = ROOT / "deploy/office/compose/docker-compose.yml"
ENV = ROOT / "rendered/env"


def run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def compose(*args: str) -> subprocess.CompletedProcess:
    return run(["docker", "compose", "-f", str(COMPOSE), "--env-file", str(ENV), *args], timeout=600)


def images() -> dict[str, str]:
    result = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"])
    found = {}
    for line in result.stdout.splitlines():
        name, _, image_id = line.partition(" ")
        if name.startswith("oi-core/") and name.endswith(":local") and name.count(":") == 1:
            found[name.split("/", 1)[1].split(":")[0]] = image_id
    return found


def commit() -> str:
    result = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    return result.stdout.strip() or "unknown"


def canonical(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def kms_client():
    sts = boto3.client("sts", region_name="us-east-1")
    credentials = sts.assume_role(RoleArn=ROLE, RoleSessionName="oi-release")["Credentials"]
    return boto3.client("kms", region_name="us-east-1",
                        aws_access_key_id=credentials["AccessKeyId"],
                        aws_secret_access_key=credentials["SecretAccessKey"],
                        aws_session_token=credentials["SessionToken"])


def build(out: Path) -> dict:
    manifest = {
        "format": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit(),
        "compose_sha256": hashlib.sha256(COMPOSE.read_bytes()).hexdigest(),
        "images": images(),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "release.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    signature = kms_client().sign(KeyId=KEY, Message=canonical(manifest), MessageType="RAW",
                                  SigningAlgorithm=ALGORITHM)["Signature"]
    (out / "release.sig").write_text(base64.b64encode(signature).decode() + "\n")
    return manifest


def verify(release: Path) -> dict:
    manifest = json.loads((release / "release.json").read_text())
    signature = base64.b64decode((release / "release.sig").read_text().strip())
    try:
        valid = kms_client().verify(KeyId=KEY, Message=canonical(manifest), MessageType="RAW",
                                    Signature=signature, SigningAlgorithm=ALGORITHM)["SignatureValid"]
    except Exception as exc:  # noqa: BLE001 - an invalid signature is a refusal, whatever KMS names it
        print(f"signature refused: {type(exc).__name__}")
        valid = False
    return {"valid": bool(valid), "manifest": manifest}


def health(timeout: int = 30) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        result = run(["docker", "exec", "api", "python", "-c",
                      "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8088/health', timeout=5).status)"])
        if result.returncode == 0 and result.stdout.strip() == "200":
            return True
        time.sleep(2)
    return False


def install(release: Path, service_names: list[str], simulate_failure: bool = False) -> int:
    checked = verify(release)
    if not checked["valid"]:
        print("refused: release signature is not valid")
        return 1
    manifest = checked["manifest"]
    wanted = {name: manifest["images"][name] for name in service_names if name in manifest["images"]}
    if not wanted:
        print("refused: no requested service is in the release")
        return 1
    current = {name: images().get(name) for name in wanted}
    rollback_path = release / "rollback.json"
    rollback_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    print("rollback recorded:", current)
    for name, image_id in wanted.items():
        run(["docker", "tag", image_id, "oi-core/" + name + ":local"])
    updated = compose("up", "-d", "--no-deps", "--force-recreate", *wanted.keys())
    if updated.returncode != 0:
        print(updated.stderr[-1000:])
        rollback(release)
        return 1
    ok = health()
    if simulate_failure:
        ok = False
        print("simulated health failure")
    if not ok:
        print("health check failed; rolling back")
        rollback(release)
        return 1
    print("installed and healthy")
    return 0


def rollback(release: Path) -> int:
    path = release / "rollback.json"
    if not path.exists():
        print("no rollback record")
        return 1
    previous = json.loads(path.read_text())
    for name, image_id in previous.items():
        if image_id:
            run(["docker", "tag", image_id, "oi-core/" + name + ":local"])
    updated = compose("up", "-d", "--no-deps", "--force-recreate", *previous.keys())
    if updated.returncode != 0:
        print(updated.stderr[-1000:])
        return 1
    if not health():
        print("rollback completed but health is still failing")
        return 1
    print("rolled back and healthy")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="signed deployment releases")
    sub = parser.add_subparsers(dest="command", required=True)
    build_p = sub.add_parser("build")
    build_p.add_argument("--out", type=Path, required=True)
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--release", type=Path, required=True)
    install_p = sub.add_parser("install")
    install_p.add_argument("--release", type=Path, required=True)
    install_p.add_argument("--services", default="api")
    install_p.add_argument("--simulate-failure", action="store_true")
    rollback_p = sub.add_parser("rollback")
    rollback_p.add_argument("--release", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        print(json.dumps(build(args.out), indent=2, sort_keys=True))
        return 0
    if args.command == "verify":
        result = verify(args.release)
        print(json.dumps({"valid": result["valid"]}, indent=2))
        return 0 if result["valid"] else 1
    if args.command == "install":
        return install(args.release, args.services.split(","), args.simulate_failure)
    return rollback(args.release)


if __name__ == "__main__":
    raise SystemExit(main())
