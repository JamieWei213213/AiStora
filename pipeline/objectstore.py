"""A very small object-store abstraction.

The pipeline only needs put/get/list/delete on keys. Wrapping boto3 behind
this lets every stage, the app and the tests run against a directory with no
AWS at all, and keeps the S3 specifics (encryption headers, pagination,
content types) in one place.
"""

from __future__ import annotations

import io
import json
import os
import shutil
from pathlib import Path
from typing import Iterator

from pipeline.config import PipelineSettings


class ObjectStoreError(RuntimeError):
    pass


class ObjectNotFound(ObjectStoreError):
    pass


def _content_type(key: str) -> str:
    lowered = key.lower()
    if lowered.endswith(".json"):
        return "application/json"
    if lowered.endswith(".parquet"):
        return "application/vnd.apache.parquet"
    if lowered.endswith(".jsonl.gz"):
        return "application/gzip"
    if lowered.endswith(".csv"):
        return "text/csv"
    return "application/octet-stream"


class ObjectStore:
    scheme = "file"

    def put_bytes(self, key: str, data: bytes) -> str:
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def put_file(self, key: str, path: str) -> str:
        with open(path, "rb") as handle:
            return self.put_bytes(key, handle.read())

    def get_file(self, key: str, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(self.get_bytes(key))
        return path

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def list(self, prefix: str) -> Iterator[str]:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def size(self, key: str) -> int:
        raise NotImplementedError

    def url(self, key: str) -> str:
        """Address usable by DuckDB / PyArrow / the app's storage layer."""
        raise NotImplementedError

    def put_bytes_if_absent(self, key: str, data: bytes) -> bool:
        """Create ``key`` only if it does not exist. Returns False when it does.

        Used as a lock: S3 honours ``If-None-Match: *`` atomically, the local
        store uses ``O_EXCL``.
        """
        raise NotImplementedError

    def copy(self, source_key: str, destination_key: str) -> str:
        return self.put_bytes(destination_key, self.get_bytes(source_key))

    # Convenience for the many small JSON artifacts.
    def put_json(self, key: str, payload) -> str:
        return self.put_bytes(
            key,
            json.dumps(payload, default=str, sort_keys=True, indent=2).encode("utf-8"),
        )

    def get_json(self, key: str):
        return json.loads(self.get_bytes(key).decode("utf-8"))


class LocalObjectStore(ObjectStore):
    scheme = "file"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        key = str(key).lstrip("/")
        if not key or ".." in key.split("/"):
            raise ObjectStoreError(f"Refusing key {key!r}.")
        return self.root / key

    def put_bytes(self, key, data):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".part")
        with open(temp, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
        return self.url(key)

    def put_file(self, key, source):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".part")
        shutil.copyfile(source, temp)
        os.replace(temp, path)
        return self.url(key)

    def get_bytes(self, key):
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return path.read_bytes()

    def get_file(self, key, destination):
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        return destination

    def exists(self, key):
        return self._path(key).is_file()

    def list(self, prefix):
        base = self._path(prefix.rstrip("/")) if prefix else self.root
        if prefix.endswith("/"):
            start = base
        else:
            start = base.parent
        if not start.exists():
            return iter(())
        keys = []
        for path in start.rglob("*"):
            if path.is_file() and not path.name.endswith(".part"):
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    keys.append(key)
        return iter(sorted(keys))

    def delete(self, key):
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def size(self, key):
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return path.stat().st_size

    def url(self, key):
        return str(self._path(key))

    def put_bytes_if_absent(self, key, data):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            return False
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return True

    def copy(self, source_key, destination_key):
        source = self._path(source_key)
        if not source.is_file():
            raise ObjectNotFound(source_key)
        return self.put_file(destination_key, str(source))


class S3ObjectStore(ObjectStore):
    scheme = "s3"

    def __init__(self, bucket: str, region: str, client=None, endpoint_url=None):
        if not bucket:
            raise ObjectStoreError("An S3 bucket name is required.")
        self.bucket = bucket
        if client is None:
            import boto3

            client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url or None)
        self.client = client

    def put_bytes(self, key, data):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=_content_type(key),
            ServerSideEncryption="AES256",
        )
        return self.url(key)

    def put_file(self, key, path):
        self.client.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={
                "ContentType": _content_type(key),
                "ServerSideEncryption": "AES256",
            },
        )
        return self.url(key)

    def get_bytes(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey as exc:
            raise ObjectNotFound(key) from exc
        return response["Body"].read()

    def get_file(self, key, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download_file(self.bucket, key, str(path))
        except Exception as exc:  # botocore ClientError for 404
            if "404" in str(exc) or "Not Found" in str(exc) or "NoSuchKey" in str(exc):
                raise ObjectNotFound(key) from exc
            raise
        return path

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                return False
            raise

    def list(self, prefix):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def size(self, key):
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise ObjectNotFound(key) from exc
        return int(head["ContentLength"])

    def url(self, key):
        return f"s3://{self.bucket}/{key}"

    def put_bytes_if_absent(self, key, data):
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=key, Body=data, IfNoneMatch="*",
                ContentType=_content_type(key), ServerSideEncryption="AES256",
            )
            return True
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
            if code in {"PreconditionFailed", "412"} or "PreconditionFailed" in str(exc) or "412" in str(exc):
                return False
            raise

    def copy(self, source_key, destination_key):
        self.client.copy_object(
            Bucket=self.bucket,
            Key=destination_key,
            CopySource={"Bucket": self.bucket, "Key": source_key},
            ServerSideEncryption="AES256",
            MetadataDirective="REPLACE",
            ContentType=_content_type(destination_key),
        )
        return self.url(destination_key)

    def open_read(self, key) -> io.BytesIO:
        return io.BytesIO(self.get_bytes(key))


def store_from_settings(settings: PipelineSettings, client=None) -> ObjectStore:
    if settings.is_aws:
        return S3ObjectStore(
            settings.lake_bucket,
            settings.aws_region,
            client=client,
            endpoint_url=settings.s3_endpoint_url,
        )
    return LocalObjectStore(settings.lake_root)
