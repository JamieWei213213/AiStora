import hashlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import current_app
from werkzeug.utils import secure_filename


class DatasetStorageError(RuntimeError):
    pass


def safe_dataset_filename(filename):
    cleaned = secure_filename(str(filename or ""))
    if not cleaned:
        raise DatasetStorageError("The dataset filename is invalid.")
    if Path(cleaned).suffix.lower() != ".csv":
        raise DatasetStorageError("AIStora currently accepts CSV datasets only.")
    return cleaned


class LocalDatasetStorage:
    backend = "local"

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, source_path, project_id, filename):
        filename = safe_dataset_filename(filename)
        destination = (
            self.root
            / str(int(project_id))
            / uuid.uuid4().hex
            / filename
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source_path), destination)
        return str(destination)

    def materialize(self, reference):
        path = Path(str(reference))
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.is_file():
            raise DatasetStorageError("The dataset object could not be found.")
        return str(path)

    def fingerprint(self, reference):
        path = Path(self.materialize(reference))
        stat = path.stat()
        return {
            "backend": self.backend,
            "size": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }

    def delete(self, reference):
        path = Path(str(reference))
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise DatasetStorageError(
                "Refusing to delete a file outside the dataset storage root."
            ) from exc
        if path.exists():
            path.unlink()


class S3DatasetStorage:
    backend = "s3"

    def __init__(
        self,
        bucket,
        prefix,
        region,
        cache_dir,
        client=None,
        endpoint_url=None,
    ):
        if not bucket:
            raise DatasetStorageError(
                "S3_DATASET_BUCKET is required when DATASET_STORAGE_BACKEND=s3."
            )
        self.bucket = str(bucket)
        self.prefix = str(prefix or "datasets").strip("/")
        self.region = str(region or "us-west-2")
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                region_name=self.region,
                endpoint_url=endpoint_url or None,
            )
        self.client = client

    def _key(self, project_id, filename):
        filename = safe_dataset_filename(filename)
        return (
            f"{self.prefix}/{int(project_id)}/{uuid.uuid4().hex}/{filename}"
        )

    def _parse(self, reference):
        parsed = urlparse(str(reference))
        key = parsed.path.lstrip("/")
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            raise DatasetStorageError(
                "The dataset reference is not in the configured S3 bucket."
            )
        if not key.startswith(f"{self.prefix}/"):
            raise DatasetStorageError(
                "The dataset reference is outside the configured S3 prefix."
            )
        return key

    def put_file(self, source_path, project_id, filename):
        key = self._key(project_id, filename)
        try:
            self.client.upload_file(
                str(source_path),
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": "text/csv",
                    "ServerSideEncryption": "AES256",
                },
            )
        except Exception as exc:
            raise DatasetStorageError(
                "The dataset could not be uploaded to S3."
            ) from exc
        return f"s3://{self.bucket}/{key}"

    def fingerprint(self, reference):
        key = self._parse(reference)
        try:
            metadata = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise DatasetStorageError(
                "The dataset object could not be read from S3."
            ) from exc
        modified = metadata.get("LastModified")
        if hasattr(modified, "isoformat"):
            modified = modified.isoformat()
        return {
            "backend": self.backend,
            "bucket": self.bucket,
            "key": key,
            "etag": str(metadata.get("ETag", "")).strip('"'),
            "version_id": metadata.get("VersionId"),
            "size": int(metadata.get("ContentLength", 0)),
            "last_modified": str(modified or ""),
        }

    def materialize(self, reference):
        metadata = self.fingerprint(reference)
        identity = "|".join(
            str(metadata.get(name) or "")
            for name in ("bucket", "key", "etag", "version_id", "size")
        )
        cache_name = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        suffix = Path(metadata["key"]).suffix or ".csv"
        destination = self.cache_dir / f"{cache_name}{suffix}"
        if destination.is_file() and destination.stat().st_size == metadata["size"]:
            return str(destination)

        handle = tempfile.NamedTemporaryFile(
            prefix="aistora-s3-",
            suffix=".download",
            dir=self.cache_dir,
            delete=False,
        )
        temp_path = Path(handle.name)
        handle.close()
        try:
            self.client.download_file(
                self.bucket,
                metadata["key"],
                str(temp_path),
            )
            os.replace(temp_path, destination)
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink()
            raise DatasetStorageError(
                "The dataset could not be downloaded from S3."
            ) from exc
        return str(destination)

    def delete(self, reference):
        key = self._parse(reference)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise DatasetStorageError(
                "The dataset object could not be deleted from S3."
            ) from exc


def _storage_signature(app):
    return (
        str(app.config.get("DATASET_STORAGE_BACKEND", "local")).lower(),
        str(app.config.get("UPLOAD_FOLDER", "uploads")),
        str(app.config.get("S3_DATASET_BUCKET", "")),
        str(app.config.get("S3_DATASET_PREFIX", "datasets")),
        str(app.config.get("AWS_REGION", "us-west-2")),
        str(app.config.get("DATASET_CACHE_DIR", "")),
        str(app.config.get("S3_ENDPOINT_URL", "")),
    )


def get_dataset_storage():
    app = current_app._get_current_object()
    signature = _storage_signature(app)
    cached = app.extensions.get("aistora_dataset_storage")
    if cached and cached[0] == signature:
        return cached[1]

    backend = signature[0]
    if backend == "local":
        storage = LocalDatasetStorage(app.config.get("UPLOAD_FOLDER", "uploads"))
    elif backend == "s3":
        storage = S3DatasetStorage(
            bucket=app.config.get("S3_DATASET_BUCKET"),
            prefix=app.config.get("S3_DATASET_PREFIX", "datasets"),
            region=app.config.get("AWS_REGION", "us-west-2"),
            cache_dir=app.config.get("DATASET_CACHE_DIR", "/tmp/aistora-cache"),
            endpoint_url=app.config.get("S3_ENDPOINT_URL"),
        )
    else:
        raise DatasetStorageError(
            "DATASET_STORAGE_BACKEND must be either local or s3."
        )
    app.extensions["aistora_dataset_storage"] = (signature, storage)
    return storage
