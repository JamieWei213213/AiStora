"""Cooperative cancellation for in-flight agent runs.

A run is executed inside one worker thread, but the cancel request arrives on a
different request and, in a multi-task deployment, very likely on a different
container. Cancellation state therefore has to be shared.

Redis is used when it is configured (the same instance that backs sessions), so
cancellation works across ECS tasks. When Redis is absent the previous
on-disk markers are used, which are correct within a single container and are
the right fit for local development.
"""

import os
import re
import threading


_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,64}")

# Long enough to outlive any run (agent timeout is well under a minute), short
# enough that abandoned markers expire on their own.
CANCELLATION_TTL_SECONDS = 900


def _redis_client():
    """Return the shared Redis client, or None outside an app context."""
    try:
        from flask import current_app

        if not current_app:
            return None
        return current_app.config.get("SESSION_REDIS")
    except Exception:
        return None


class CancellationSignal:
    def __init__(self, event, cancel_path, request_id, registry):
        self.event = event
        self.cancel_path = cancel_path
        self.request_id = request_id
        self.registry = registry

    def is_set(self):
        if self.event.is_set():
            return True
        if os.path.exists(self.cancel_path):
            return True
        client = _redis_client()
        if client is not None:
            try:
                return bool(client.exists(self.registry.cancel_key(self.request_id)))
            except Exception:
                return False
        return False


class CancellationRegistry:
    """Tracks cancellation in memory, in Redis when available, and on disk."""

    def __init__(self, marker_dir=None, key_prefix="aistora:cancel:"):
        self._events = {}
        self._lock = threading.Lock()
        self.key_prefix = key_prefix
        self.marker_dir = marker_dir or os.environ.get(
            "AGENT_CANCELLATION_DIR",
            "instance/agent_cancellations",
        )

    def cancel_key(self, request_id):
        return f"{self.key_prefix}{request_id}:cancel"

    def active_key(self, request_id):
        return f"{self.key_prefix}{request_id}:active"

    def _paths(self, request_id):
        if not _VALID_REQUEST_ID.fullmatch(request_id):
            raise ValueError("Invalid cancellation request ID.")
        return (
            os.path.join(self.marker_dir, f"{request_id}.active"),
            os.path.join(self.marker_dir, f"{request_id}.cancel"),
        )

    def register(self, request_id):
        with self._lock:
            os.makedirs(self.marker_dir, exist_ok=True)
            active_path, cancel_path = self._paths(request_id)
            if os.path.exists(cancel_path):
                os.remove(cancel_path)
            with open(active_path, "w", encoding="utf-8") as marker:
                marker.write("active")
            client = _redis_client()
            if client is not None:
                try:
                    client.delete(self.cancel_key(request_id))
                    client.setex(
                        self.active_key(request_id),
                        CANCELLATION_TTL_SECONDS,
                        "1",
                    )
                except Exception:
                    pass
            event = threading.Event()
            self._events[request_id] = event
            return CancellationSignal(event, cancel_path, request_id, self)

    def cancel(self, request_id):
        with self._lock:
            active_path, cancel_path = self._paths(request_id)
            event = self._events.get(request_id)
            client = _redis_client()

            known = event is not None or os.path.exists(active_path)
            if client is not None and not known:
                try:
                    known = bool(client.exists(self.active_key(request_id)))
                except Exception:
                    known = False
            if not known:
                return False

            if event is not None:
                event.set()
            os.makedirs(self.marker_dir, exist_ok=True)
            with open(cancel_path, "w", encoding="utf-8") as marker:
                marker.write("cancelled")
            if client is not None:
                try:
                    client.setex(
                        self.cancel_key(request_id),
                        CANCELLATION_TTL_SECONDS,
                        "1",
                    )
                except Exception:
                    pass
            return True

    def clear(self, request_id):
        with self._lock:
            self._events.pop(request_id, None)
            active_path, cancel_path = self._paths(request_id)
            for path in (active_path, cancel_path):
                if os.path.exists(path):
                    os.remove(path)
            client = _redis_client()
            if client is not None:
                try:
                    client.delete(
                        self.active_key(request_id),
                        self.cancel_key(request_id),
                    )
                except Exception:
                    pass


cancellation_registry = CancellationRegistry()
