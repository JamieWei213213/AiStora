"""Where connector secrets come from.

AWS: an SSM Parameter Store ``SecureString`` named by ``secret_ref`` (free,
KMS-encrypted, scoped by IAM to the pipeline role). Local: an environment
variable of that name. The value itself is never written to the lake.
"""

from __future__ import annotations

import os

from pipeline.config import PipelineSettings
from pipeline.connectors.base import ConnectorError


def resolve_secret(settings: PipelineSettings, secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    if settings.is_aws:
        import boto3

        client = boto3.client("ssm", region_name=settings.aws_region)
        try:
            response = client.get_parameter(Name=secret_ref, WithDecryption=True)
        except Exception as exc:
            raise ConnectorError(f"Could not read SSM parameter {secret_ref!r}.") from exc
        return response["Parameter"]["Value"]
    value = os.environ.get(secret_ref)
    if value is None:
        raise ConnectorError(f"Environment variable {secret_ref!r} is not set.")
    return value
