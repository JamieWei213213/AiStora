import os
import re
import threading


class CancellationSignal:
    def __init__(self, event, cancel_path):
        self.event = event
        self.cancel_path = cancel_path

    def is_set(self):
        return self.event.is_set() or os.path.exists(self.cancel_path)


class CancellationRegistry:
    """Tracks cancellation in memory and on disk so Gunicorn workers can share it."""

    def __init__(self, marker_dir=None):
        self._events = {}
        self._lock = threading.Lock()
        self.marker_dir = marker_dir or os.environ.get(
            "AGENT_CANCELLATION_DIR",
            "instance/agent_cancellations",
        )

    def _paths(self, request_id):
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", request_id):
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
            event = threading.Event()
            self._events[request_id] = event
            return CancellationSignal(event, cancel_path)

    def cancel(self, request_id):
        with self._lock:
            active_path, cancel_path = self._paths(request_id)
            event = self._events.get(request_id)
            if event is None and not os.path.exists(active_path):
                return False
            if event is not None:
                event.set()
            os.makedirs(self.marker_dir, exist_ok=True)
            with open(cancel_path, "w", encoding="utf-8") as marker:
                marker.write("cancelled")
            return True

    def clear(self, request_id):
        with self._lock:
            self._events.pop(request_id, None)
            active_path, cancel_path = self._paths(request_id)
            for path in (active_path, cancel_path):
                if os.path.exists(path):
                    os.remove(path)


cancellation_registry = CancellationRegistry()
