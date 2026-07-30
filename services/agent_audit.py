import json
import os
import threading
from datetime import datetime, timezone


_write_lock = threading.Lock()


class AgentAuditLogger:
    """Append-only audit log that deliberately excludes row and filter values."""

    def __init__(self, path=None):
        self.path = path or os.environ.get(
            "AGENT_AUDIT_PATH",
            "instance/agent_audit.jsonl",
        )

    def record(self, request_id, user_id, tool_name, arguments, status, duration_ms):
        safe_arguments = {
            key: value
            for key, value in arguments.items()
            if key in {
                "source", "left", "right", "save_as", "column", "columns",
                "group_by", "value_column", "operation", "sort_column",
                "chart_type", "limit", "descending",
            }
        }
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "user_id": user_id,
            "tool": tool_name,
            "arguments": safe_arguments,
            "status": status,
            "duration_ms": duration_ms,
        }
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with _write_lock:
            with open(self.path, "a", encoding="utf-8") as audit_file:
                audit_file.write(json.dumps(entry, default=str) + "\n")
