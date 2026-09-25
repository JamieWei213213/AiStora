"""Product telemetry: structured events, batched to the lake as JSONL.

The app used to write an audit JSONL to ``/tmp`` that was lost on every
restart. Events now go to ``events/dt=.../hour=.../*.jsonl.gz`` in the lake,
which dbt reads nightly. Nothing here carries a cell value: callers pass
metadata (counts, names, durations, statuses) and the privacy rules of the
agent still apply upstream.

The sink is a process-wide singleton so Gunicorn workers each keep one
buffer. Flushes happen on size, on a timer thread, and at interpreter exit;
a failed flush is logged and the batch is dropped rather than blocking a
request -- telemetry is never load-bearing.
"""

from __future__ import annotations

import atexit
import gzip
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone

from pipeline.config import PipelineSettings, settings_from_env
from pipeline.ids import new_ulid
from pipeline.keys import events_key
from pipeline.objectstore import ObjectStore, store_from_settings

logger = logging.getLogger(__name__)

_sink: "EventSink | None" = None
_sink_lock = threading.Lock()
_disabled = False


class EventSink:
    def __init__(self, settings: PipelineSettings, store: ObjectStore | None = None, *, host: str | None = None):
        self.settings = settings
        self.store = store
        self.host = (host or socket.gethostname() or "app")[:40].replace("/", "_")
        self._buffer: list[dict] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._timer: threading.Thread | None = None
        self._stop = threading.Event()
        self.enabled = bool(settings.events_enabled)
        self.dropped = 0
        self.flushed_batches = 0

    # ----- producing --------------------------------------------------------
    def emit(self, name: str, **fields) -> None:
        if not self.enabled:
            return
        event = {
            "event": str(name),
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "host": self.host,
        }
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                event[key] = value
            else:
                event[key] = json.loads(json.dumps(value, default=str))
        with self._lock:
            self._buffer.append(event)
            should_flush = len(self._buffer) >= self.settings.events_flush_rows
        if should_flush:
            self.flush()

    # ----- flushing ---------------------------------------------------------
    def flush(self) -> int:
        with self._lock:
            batch, self._buffer = self._buffer, []
            self._last_flush = time.monotonic()
        if not batch:
            return 0
        if self.store is None:
            try:
                self.store = store_from_settings(self.settings)
            except Exception:  # pragma: no cover - misconfiguration
                logger.exception("Event sink has no store; dropping %s events", len(batch))
                self.dropped += len(batch)
                return 0
        now = datetime.now(timezone.utc)
        key = events_key(now.strftime("%Y-%m-%d"), now.strftime("%H"), self.host, new_ulid())
        body = "\n".join(json.dumps(event, separators=(",", ":"), default=str) for event in batch) + "\n"
        try:
            self.store.put_bytes(key, gzip.compress(body.encode("utf-8")))
            self.flushed_batches += 1
            return len(batch)
        except Exception:
            logger.exception("Could not write %s events to the lake", len(batch))
            self.dropped += len(batch)
            return 0

    def start_timer(self) -> None:
        if self._timer is not None or not self.enabled:
            return

        def loop():
            while not self._stop.wait(min(self.settings.events_flush_seconds, 5.0)):
                if time.monotonic() - self._last_flush >= self.settings.events_flush_seconds:
                    self.flush()

        self._timer = threading.Thread(target=loop, name="aistora-events", daemon=True)
        self._timer.start()

    def close(self) -> None:
        self._stop.set()
        self.flush()

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._buffer)


def get_sink() -> EventSink:
    global _sink
    with _sink_lock:
        if _sink is None and _disabled:
            _sink = EventSink(PipelineSettings(events_enabled=False))
        if _sink is None:
            try:
                settings = settings_from_env()
            except Exception:
                settings = PipelineSettings(events_enabled=False)
            _sink = EventSink(settings)
            _sink.start_timer()
            atexit.register(_sink.close)
        return _sink


def configure_sink(sink: EventSink | None) -> None:
    """Replace the process sink (tests, or an app that built its own store)."""
    global _sink, _disabled
    with _sink_lock:
        if _sink is not None:
            _sink.close()
        _sink = sink
        _disabled = sink is None
        if sink is not None:
            sink.start_timer()


def emit(name: str, **fields) -> None:
    try:
        get_sink().emit(name, **fields)
    except Exception:  # pragma: no cover - never let telemetry raise
        logger.debug("event emit failed", exc_info=True)


def flush() -> int:
    return get_sink().flush()
