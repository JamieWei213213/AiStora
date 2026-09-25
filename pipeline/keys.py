"""Object-key layout for the lake.

One place defines where everything lives so that Terraform lifecycle rules,
IAM prefixes, EventBridge filters and the code all agree::

    raw/project=<id>/dataset=<slug>/load_id=<ulid>/<file>.csv   as uploaded
    silver/project=<id>/dataset=<slug>/load_id=<ulid>/part-0.parquet
    curated/project=<id>/dataset=<slug>/current/part-0.parquet  app reads this
    iceberg/<namespace>/<table>/...                             PyIceberg-managed
    quarantine/project=<id>/dataset=<slug>/load_id=<ulid>/{file, report.json}
    contracts/project=<id>/dataset=<slug>/contract.json
    manifests/project=<id>/dataset=<slug>/<ulid>.json
    work/project=<id>/dataset=<slug>/load_id=<ulid>/working.csv  between stages, 1-day lifecycle
    locks/project=<id>/dataset=<slug>/curate.lock               per-dataset write lock
    events/dt=YYYY-MM-DD/hour=HH/<host>-<ulid>.jsonl.gz
    gold/<mart>.parquet
    connectors/<id>/{config.json, state.json}
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RAW_PREFIX = "raw"
SILVER_PREFIX = "silver"
CURATED_PREFIX = "curated"
ICEBERG_PREFIX = "iceberg"
QUARANTINE_PREFIX = "quarantine"
CONTRACTS_PREFIX = "contracts"
MANIFESTS_PREFIX = "manifests"
EVENTS_PREFIX = "events"
GOLD_PREFIX = "gold"
CONNECTORS_PREFIX = "connectors"
WORK_PREFIX = "work"
LOCKS_PREFIX = "locks"

# The prefixes the *app* is allowed to read datasets from. ``datasets/`` is
# the pre-pipeline layout and stays readable so existing tables keep working.
APP_READABLE_PREFIXES = ("datasets", CURATED_PREFIX, SILVER_PREFIX)

_DATASET_SLUG = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
_RAW_KEY = re.compile(
    rf"^{RAW_PREFIX}/project=(?P<project>\d+)/dataset=(?P<dataset>[a-z0-9_]+)/"
    rf"load_id=(?P<load_id>[0-9A-HJKMNP-TV-Z]{{26}})/(?P<filename>[^/]+)$"
)


class InvalidKeyError(ValueError):
    pass


def dataset_slug(name: str) -> str:
    """Turn a file or table name into a stable dataset slug.

    ``Q3 Invoices (final).csv`` becomes ``q3_invoices_final``. The slug is
    what the Iceberg table, the Glue table and every prefix are named after,
    so it is restricted to characters every one of those accepts.
    """
    stem = re.sub(r"\.(csv|parquet|tsv|txt)$", "", str(name or "").strip(), flags=re.I)
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:63]
    if not slug or not slug[0].isalnum():
        slug = f"dataset_{slug}" if slug else "dataset"
    if not _DATASET_SLUG.match(slug):
        raise InvalidKeyError(f"Could not derive a dataset name from {name!r}.")
    return slug


def validate_dataset_slug(slug: str) -> str:
    if not _DATASET_SLUG.match(str(slug or "")):
        raise InvalidKeyError(
            "Dataset names must be 1-63 lowercase letters, digits or "
            "underscores and start with a letter or digit."
        )
    return slug


@dataclass(frozen=True)
class LoadRef:
    project_id: int
    dataset: str
    load_id: str

    @property
    def partition(self) -> str:
        return f"project={int(self.project_id)}/dataset={self.dataset}"

    def raw_key(self, filename: str) -> str:
        return f"{RAW_PREFIX}/{self.partition}/load_id={self.load_id}/{filename}"

    def silver_key(self, part: int = 0) -> str:
        return f"{SILVER_PREFIX}/{self.partition}/load_id={self.load_id}/part-{part}.parquet"

    def silver_prefix(self) -> str:
        return f"{SILVER_PREFIX}/{self.partition}/load_id={self.load_id}/"

    def quarantine_key(self, filename: str) -> str:
        return f"{QUARANTINE_PREFIX}/{self.partition}/load_id={self.load_id}/{filename}"

    def manifest_key(self) -> str:
        return f"{MANIFESTS_PREFIX}/{self.partition}/{self.load_id}.json"

    def work_key(self, name: str) -> str:
        """Intermediate files handed between stages (expired by lifecycle)."""
        return f"{WORK_PREFIX}/{self.partition}/load_id={self.load_id}/{name}"

    def lock_key(self) -> str:
        return f"{LOCKS_PREFIX}/{self.partition}/curate.lock"


def curated_key(project_id: int, dataset: str, part: int = 0) -> str:
    return f"{CURATED_PREFIX}/project={int(project_id)}/dataset={dataset}/current/part-{part}.parquet"


def contract_key(project_id: int, dataset: str) -> str:
    return f"{CONTRACTS_PREFIX}/project={int(project_id)}/dataset={dataset}/contract.json"


def manifests_prefix(project_id: int, dataset: str | None = None) -> str:
    base = f"{MANIFESTS_PREFIX}/project={int(project_id)}/"
    return base if dataset is None else f"{base}dataset={dataset}/"


def events_key(dt: str, hour: str, host: str, ulid: str) -> str:
    return f"{EVENTS_PREFIX}/dt={dt}/hour={hour}/{host}-{ulid}.jsonl.gz"


def gold_key(mart: str) -> str:
    return f"{GOLD_PREFIX}/{mart}.parquet"


def connector_key(connector_id: str, name: str) -> str:
    if not re.match(r"^[a-z0-9][a-z0-9_-]{0,62}$", str(connector_id or "")):
        raise InvalidKeyError("Connector ids are lowercase letters, digits, - and _.")
    return f"{CONNECTORS_PREFIX}/{connector_id}/{name}"


def parse_raw_key(key: str) -> tuple[LoadRef, str]:
    """Return ``(LoadRef, filename)`` for an object under ``raw/``.

    Anything else raises: the state machine is triggered by every object
    created under ``raw/`` and must refuse keys that were not written by the
    app or a connector rather than guess at them.
    """
    match = _RAW_KEY.match(str(key or ""))
    if not match:
        raise InvalidKeyError(f"Not a raw dataset key: {key!r}")
    ref = LoadRef(
        project_id=int(match.group("project")),
        dataset=match.group("dataset"),
        load_id=match.group("load_id"),
    )
    return ref, match.group("filename")


def iceberg_table_name(project_id: int, dataset: str) -> str:
    return f"p{int(project_id)}__{dataset}"


def iceberg_history_table_name(project_id: int, dataset: str) -> str:
    return f"{iceberg_table_name(project_id, dataset)}__history"
