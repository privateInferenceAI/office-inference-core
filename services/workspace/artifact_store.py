"""The S3 artifact store: the same content-addressed objects, in an object store.

**The local store writes under the task and removes it with the sandbox.** This store uploads each file under
`<prefix>/<task-id>/<sha256>` before cleanup, so a client can keep artifacts after the task is gone. The API
serves the bytes through the same hash route either way.

The client is injected so the unit tests do not need a bucket, and so a deployment can point the service at the
client's account with a role instead of a long-lived key.
"""
from __future__ import annotations

from pathlib import Path


class ArtifactStoreError(Exception):
    """The object store refused a put or a get."""


class S3ArtifactStore:
    def __init__(self, *, bucket: str, endpoint_url: str = "", region: str = "us-east-1",
                 access_key: str = "", secret_key: str = "", prefix: str = "artifacts",
                 client=None):
        if not bucket:
            raise ValueError("an S3 artifact store needs a bucket")
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.prefix = prefix.strip("/")
        self._client = client

    def client(self):
        if self._client is None:
            import boto3  # imported lazily so the local store path has no S3 dependency
            self._client = boto3.client(
                "s3", endpoint_url=self.endpoint_url or None, region_name=self.region,
                aws_access_key_id=self.access_key or None,
                aws_secret_access_key=self.secret_key or None)
        return self._client

    def key(self, task_id: str, sha256: str) -> str:
        return f"{self.prefix}/{task_id}/{sha256}" if self.prefix else f"{task_id}/{sha256}"

    def put(self, task_id: str, source: str | Path, records: list[dict]) -> list[dict]:
        """Upload the files named by the local records; return the records with their object keys."""
        base = Path(source)
        uploaded = []
        for record in records:
            # The local copy is content-addressed by the sandbox, so the file on disk is named by its hash.
            path = base / record["sha256"]
            try:
                data = path.read_bytes()
                self.client().put_object(Bucket=self.bucket, Key=self.key(task_id, record["sha256"]),
                                         Body=data, ContentType="application/octet-stream")
            except Exception as exc:  # noqa: BLE001
                raise ArtifactStoreError(f"artifact upload failed: {type(exc).__name__}") from exc
            uploaded.append(dict(record, key=self.key(task_id, record["sha256"]), store="s3"))
        return uploaded

    def get(self, task_id: str, sha256: str) -> bytes:
        try:
            response = self.client().get_object(Bucket=self.bucket, Key=self.key(task_id, sha256))
            return response["Body"].read()
        except Exception as exc:  # noqa: BLE001
            raise ArtifactStoreError(f"artifact download failed: {type(exc).__name__}") from exc
