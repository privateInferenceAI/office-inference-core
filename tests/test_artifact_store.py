"""The S3 artifact store: content-addressed keys, upload and download."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "workspace"))

import artifact_store  # noqa: E402


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(("put", kwargs["Key"]))
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs["Key"]))
        if kwargs["Key"] not in self.objects:
            raise KeyError("no such key")
        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}


def test_put_uploads_each_file_under_a_content_addressed_key(tmp_path):
    fake = FakeS3()
    store = artifact_store.S3ArtifactStore(bucket="oi-artifacts", prefix="artifacts", client=fake)
    source = tmp_path / "task"
    source.mkdir()
    (source / ("a" * 64)).write_text("hello")
    records = [{"path": "report.txt", "sha256": "a" * 64, "bytes": 5}]

    uploaded = store.put("task-1", source, records)

    assert uploaded[0]["key"] == "artifacts/task-1/" + "a" * 64
    assert uploaded[0]["store"] == "s3"
    assert fake.objects["artifacts/task-1/" + "a" * 64] == b"hello"


def test_get_returns_the_bytes_by_hash(tmp_path):
    fake = FakeS3()
    fake.objects["artifacts/task-1/" + "b" * 64] = b"stored"
    store = artifact_store.S3ArtifactStore(bucket="oi-artifacts", client=fake)
    assert store.get("task-1", "b" * 64) == b"stored"


def test_a_store_needs_a_bucket():
    with pytest.raises(ValueError):
        artifact_store.S3ArtifactStore(bucket="")


def test_an_upload_failure_is_typed(tmp_path):
    class Broken(FakeS3):
        def put_object(self, **kwargs):
            raise OSError("no route")

    source = tmp_path / "task"
    source.mkdir()
    (source / "x").write_text("x")
    store = artifact_store.S3ArtifactStore(bucket="b", client=Broken())
    with pytest.raises(artifact_store.ArtifactStoreError):
        store.put("t", source, [{"path": "x", "sha256": "c" * 64, "bytes": 1}])
