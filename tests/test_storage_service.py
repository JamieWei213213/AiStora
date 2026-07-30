from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.storage_service import (
    DatasetStorageError,
    LocalDatasetStorage,
    S3DatasetStorage,
)


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.upload_args = None
        self.downloads = 0

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        self.objects[(bucket, key)] = Path(filename).read_bytes()
        self.upload_args = ExtraArgs

    def head_object(self, Bucket, Key):
        value = self.objects[(Bucket, Key)]
        return {
            "ETag": '"test-etag"',
            "ContentLength": len(value),
            "LastModified": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }

    def download_file(self, bucket, key, filename):
        self.downloads += 1
        Path(filename).write_bytes(self.objects[(bucket, key)])

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_s3_storage_uploads_encrypted_materializes_and_deletes(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("region,amount\nWest,10\n", encoding="utf-8")
    client = FakeS3Client()
    storage = S3DatasetStorage(
        bucket="private-datasets",
        prefix="datasets",
        region="us-west-2",
        cache_dir=tmp_path / "cache",
        client=client,
    )

    reference = storage.put_file(source, 42, "sales.csv")
    assert reference.startswith("s3://private-datasets/datasets/42/")
    assert client.upload_args == {
        "ContentType": "text/csv",
        "ServerSideEncryption": "AES256",
    }
    assert storage.fingerprint(reference)["etag"] == "test-etag"

    first_path = storage.materialize(reference)
    second_path = storage.materialize(reference)
    assert Path(first_path).read_bytes() == source.read_bytes()
    assert second_path == first_path
    assert client.downloads == 1

    storage.delete(reference)
    assert client.objects == {}


def test_storage_rejects_unsafe_files_and_out_of_scope_deletes(tmp_path):
    storage = LocalDatasetStorage(tmp_path / "datasets")
    source = tmp_path / "source.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    stored = storage.put_file(source, 1, "../../safe.csv")
    assert Path(stored).name == "safe.csv"

    with pytest.raises(DatasetStorageError):
        storage.put_file(source, 1, "notes.txt")
    with pytest.raises(DatasetStorageError):
        storage.delete(source)
