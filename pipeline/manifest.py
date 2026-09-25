"""The load manifest: one JSON document per load, the pipeline's ledger.

The manifest is the only state the stages share. Step Functions passes a
tiny payload (``project_id``, ``dataset``, ``load_id``) between states and
each state reads the manifest, does its work, and writes it back. The app
syncs its ``Load`` rows from these documents, so the pipeline never needs a
connection to the app's database and keeps working while the app is down.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from pipeline.keys import LoadRef
from pipeline.objectstore import ObjectNotFound, ObjectStore

LOAD_MODES = ("replace", "append", "merge")

STATUS_RECEIVED = "received"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_QUARANTINED = "quarantined"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = {STATUS_SUCCEEDED, STATUS_QUARANTINED, STATUS_FAILED}

STAGES = ("validate", "profile", "gate", "transform", "curate", "register")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class LoadManifest:
    load_id: str
    project_id: int
    dataset: str
    mode: str = "replace"
    key_columns: list[str] = field(default_factory=list)
    keep_history: bool = False
    source: str = "upload"            # upload | connector:<id>
    original_filename: str = ""
    raw_key: str = ""
    raw_bytes: int = 0
    silver_key: str | None = None
    curated_key: str | None = None
    quarantine_key: str | None = None
    status: str = STATUS_RECEIVED
    stage: str | None = None          # last stage that ran
    stages: dict = field(default_factory=dict)   # stage -> {started_at, finished_at, duration_ms, status}
    counts: dict = field(default_factory=dict)   # rows_in, rows_rejected, rows_out, rows_inserted, rows_updated, rows_skipped
    encoding: str | None = None
    delimiter: str | None = None
    columns: list[dict] = field(default_factory=list)   # contract columns after evolution
    contract: dict = field(default_factory=dict)        # full evolved contract (persisted on success)
    reset_schema: bool = False                          # replace load that reset a conflicting schema
    header_renames: dict = field(default_factory=dict)  # raw header -> sanitized name
    schema_changes: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)         # QualityReport.to_dict()
    snapshot_id: int | None = None
    parent_snapshot_id: int | None = None
    error: dict | None = None
    execution_id: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)
    finished_at: str | None = None

    # ----- identity -------------------------------------------------------
    @property
    def ref(self) -> LoadRef:
        return LoadRef(project_id=self.project_id, dataset=self.dataset, load_id=self.load_id)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    # ----- stage bookkeeping ----------------------------------------------
    def start_stage(self, name: str) -> None:
        self.stage = name
        self.status = STATUS_RUNNING
        self.stages[name] = {"started_at": utcnow_iso(), "status": "running", "_t0": time.perf_counter()}
        self.updated_at = utcnow_iso()

    def finish_stage(self, name: str, status: str = "succeeded") -> None:
        entry = self.stages.setdefault(name, {})
        t0 = entry.pop("_t0", None)
        entry["finished_at"] = utcnow_iso()
        entry["status"] = status
        if t0 is not None:
            entry["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        self.updated_at = utcnow_iso()

    def total_duration_ms(self) -> int:
        return sum(int(entry.get("duration_ms", 0) or 0) for entry in self.stages.values())

    def mark_succeeded(self) -> None:
        self.status = STATUS_SUCCEEDED
        self.finished_at = utcnow_iso()
        self.updated_at = self.finished_at
        self.error = None

    def mark_quarantined(self, reason: str, error_type: str = "quality") -> None:
        self.status = STATUS_QUARANTINED
        self.finished_at = utcnow_iso()
        self.updated_at = self.finished_at
        self.error = {"type": error_type, "message": str(reason)[:1000]}

    def mark_failed(self, reason: str, error_type: str = "internal") -> None:
        self.status = STATUS_FAILED
        self.finished_at = utcnow_iso()
        self.updated_at = self.finished_at
        self.error = {"type": error_type, "message": str(reason)[:1000]}

    # ----- persistence ----------------------------------------------------
    def to_dict(self) -> dict:
        payload = asdict(self)
        for entry in payload.get("stages", {}).values():
            entry.pop("_t0", None)
        payload["duration_ms"] = self.total_duration_ms()
        payload["manifest_version"] = 1
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "LoadManifest":
        fields = {name for name in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {key: value for key, value in dict(payload).items() if key in fields}
        return cls(**data)

    def save(self, store: ObjectStore) -> str:
        self.updated_at = utcnow_iso()
        return store.put_json(self.ref.manifest_key(), self.to_dict())

    @classmethod
    def load(cls, store: ObjectStore, ref: LoadRef) -> "LoadManifest":
        try:
            return cls.from_dict(store.get_json(ref.manifest_key()))
        except ObjectNotFound:
            raise ManifestNotFound(ref.manifest_key())

    def payload(self) -> dict:
        """What travels between Step Functions states."""
        return {
            "project_id": int(self.project_id),
            "dataset": self.dataset,
            "load_id": self.load_id,
            "status": self.status,
            "stage": self.stage,
        }


class ManifestNotFound(LookupError):
    pass


def list_manifests(store: ObjectStore, project_id: int, dataset: str | None = None) -> list[LoadManifest]:
    from pipeline.keys import manifests_prefix

    manifests = []
    for key in store.list(manifests_prefix(project_id, dataset)):
        if not key.endswith(".json"):
            continue
        try:
            manifests.append(LoadManifest.from_dict(store.get_json(key)))
        except (ValueError, TypeError, ObjectNotFound):
            continue
    manifests.sort(key=lambda item: item.load_id)
    return manifests
