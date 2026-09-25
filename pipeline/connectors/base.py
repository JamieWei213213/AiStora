"""Connectors: scheduled extracts that land files in ``raw/``.

A connector is only a producer. It writes a CSV to the raw prefix with a
manifest exactly as an upload does, and the same six stages take it from
there. That keeps connectors small (extract + cursor bookkeeping) and means
every load, whatever its origin, is validated, typed, gated and versioned
the same way.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from pipeline.keys import InvalidKeyError, connector_key, dataset_slug
from pipeline.manifest import LOAD_MODES, utcnow_iso
from pipeline.objectstore import ObjectNotFound, ObjectStore

CONNECTOR_TYPES = ("postgres", "google_sheets")


class ConnectorError(RuntimeError):
    """Configuration or extraction problem. Message is safe to show."""


@dataclass
class ConnectorConfig:
    id: str
    type: str
    project_id: int
    dataset: str
    mode: str = "replace"
    key_columns: list[str] = field(default_factory=list)
    keep_history: bool = False
    schedule: str = "rate(1 day)"          # informational; EventBridge Scheduler owns the real one
    options: dict = field(default_factory=dict)
    secret_ref: str | None = None          # SSM parameter name (aws) or env var name (local)
    enabled: bool = True
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def validate(self) -> "ConnectorConfig":
        if self.type not in CONNECTOR_TYPES:
            raise ConnectorError(f"Connector type must be one of {', '.join(CONNECTOR_TYPES)}.")
        if self.mode not in LOAD_MODES:
            raise ConnectorError(f"Load mode must be one of {', '.join(LOAD_MODES)}.")
        if self.mode == "merge" and not self.key_columns:
            raise ConnectorError("A merge connector needs at least one key column.")
        try:
            self.dataset = dataset_slug(self.dataset)
            connector_key(self.id, "config.json")  # validates the id
        except InvalidKeyError as exc:
            raise ConnectorError(str(exc)) from exc
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "ConnectorConfig":
        names = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in names}).validate()

    # ----- persistence --------------------------------------------------
    def save(self, store: ObjectStore) -> None:
        self.updated_at = utcnow_iso()
        store.put_json(connector_key(self.id, "config.json"), self.to_dict())

    @classmethod
    def load(cls, store: ObjectStore, connector_id: str) -> "ConnectorConfig":
        try:
            return cls.from_dict(store.get_json(connector_key(connector_id, "config.json")))
        except ObjectNotFound as exc:
            raise ConnectorError(f"Connector {connector_id!r} does not exist.") from exc

    @classmethod
    def list_all(cls, store: ObjectStore, project_id: int | None = None) -> list["ConnectorConfig"]:
        out = []
        for key in store.list("connectors/"):
            if not key.endswith("/config.json"):
                continue
            try:
                config = cls.from_dict(store.get_json(key))
            except (ConnectorError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if project_id is None or int(config.project_id) == int(project_id):
                out.append(config)
        return sorted(out, key=lambda c: c.id)

    def delete(self, store: ObjectStore) -> None:
        for name in ("config.json", "state.json"):
            store.delete(connector_key(self.id, name))


@dataclass
class ConnectorState:
    cursor: str | int | None = None
    last_run_at: str | None = None
    last_status: str | None = None       # succeeded | empty | failed
    last_error: str | None = None
    last_load_id: str | None = None
    last_rows: int = 0
    runs: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, store: ObjectStore, connector_id: str) -> "ConnectorState":
        try:
            payload = store.get_json(connector_key(connector_id, "state.json"))
        except ObjectNotFound:
            return cls()
        names = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in names})

    def save(self, store: ObjectStore, connector_id: str) -> None:
        store.put_json(connector_key(connector_id, "state.json"), self.to_dict())


@dataclass
class ExtractResult:
    path: str | None            # CSV on local disk, or None when nothing new
    rows: int
    cursor: str | int | None
    filename: str = "extract.csv"
    details: dict = field(default_factory=dict)


class Connector:
    """Implementations override ``extract``; everything else is shared."""

    type: str = ""

    def __init__(self, config: ConnectorConfig, secret: str | None):
        self.config = config
        self.secret = secret

    def extract(self, state: ConnectorState, workdir: str) -> ExtractResult:
        raise NotImplementedError
