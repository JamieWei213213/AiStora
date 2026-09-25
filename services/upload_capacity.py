"""Best-effort free-space guard, not a replacement for disk monitoring or quotas."""
import shutil
from pathlib import Path
from flask import current_app, jsonify, request


def check_upload_capacity():
    if request.method != "POST" or request.path not in {"/api/upload", "/api/loads"}:
        return None
    minimum = int(current_app.config.get("UPLOAD_MIN_FREE_BYTES", 0) or 0)
    if not minimum or current_app.config.get("DATASET_STORAGE_BACKEND", "local") != "local":
        return None
    folders = [current_app.config["UPLOAD_FOLDER"]]
    if current_app.config.get("PIPELINE_ENABLED"):
        folders.append(current_app.config["LAKE_ROOT"])
    required = minimum + 4 * int(request.content_length or current_app.config["MAX_CONTENT_LENGTH"])
    for folder in folders:
        path = Path(folder).resolve()
        while not path.exists() and path != path.parent:
            path = path.parent
        if shutil.disk_usage(path).free < required:
            return jsonify(success=False, error="Uploads are temporarily paused because storage is almost full. Please try again later.", error_type="storage_capacity"), 507
    return None
