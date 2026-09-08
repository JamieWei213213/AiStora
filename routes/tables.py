# routes/tables.py
import os
import tempfile
import time
import uuid

from flask import Blueprint, request, jsonify, session, current_app
from werkzeug.utils import secure_filename

from engine.dataframe import DataFrame
from extensions import db
from models import Table, Project
from services.agent_audit import AgentAuditLogger
from services.data_cleaning_agent import (
    CleaningLimitExceeded,
    build_cleaning_preview,
    write_cleaned_copy,
)
from services.schema_service import build_project_schema
from services.storage_service import DatasetStorageError, get_dataset_storage
from services.validation import ValidationError, validate_table_name

tables_bp = Blueprint('tables', __name__)

def get_table_if_owner(table_id, user_id):
    """Security check: Ensures the user owns the table."""
    table = db.session.get(Table, table_id)
    if not table:
        return None
    project = db.session.get(Project, table.project_id)
    if not project or project.user_id != user_id:
        return None
    return table


def _unique_cleaned_names(table):
    base_table_name = f"{table.name}_cleaned"
    table_name = base_table_name
    suffix = 2
    while Table.query.filter_by(project_id=table.project_id, name=table_name).first():
        table_name = f"{base_table_name}_{suffix}"
        suffix += 1

    original_stem = os.path.splitext(table.filename)[0]
    safe_stem = secure_filename(original_stem) or "table"
    filename = f"{safe_stem}_cleaned.csv"
    file_suffix = 2
    while Table.query.filter_by(
        project_id=table.project_id,
        filename=filename,
    ).first():
        filename = f"{safe_stem}_cleaned_{file_suffix}.csv"
        file_suffix += 1
    return table_name, filename

@tables_bp.route('/api/tables/<int:id>', methods=['PUT'])
def rename_table(id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    table = get_table_if_owner(id, user_id)
    if not table:
        return jsonify({'success': False, 'error': 'Table not found'}), 404

    data = request.get_json(silent=True) or {}
    try:
        new_name = validate_table_name(data.get('name'))
    except ValidationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    
    # Check for name collision in the same project
    exists = Table.query.filter_by(project_id=table.project_id, name=new_name).first()
    if exists:
        return jsonify({'success': False, 'error': 'A table with this name already exists'}), 409
    
    table.name = new_name
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Table renamed'})

@tables_bp.route('/api/tables/<int:id>', methods=['DELETE'])
def delete_table(id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    table = get_table_if_owner(id, user_id)
    if not table:
        return jsonify({'success': False, 'error': 'Table not found'}), 404

    try:
        get_dataset_storage().delete(table.filepath)
        db.session.delete(table)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Table deleted'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@tables_bp.route("/api/tables/<int:id>/clean/preview", methods=["POST"])
def preview_table_cleaning(id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    table = get_table_if_owner(id, user_id)
    if not table:
        return jsonify({"success": False, "error": "Table not found"}), 404

    try:
        storage = get_dataset_storage()
        local_path = storage.materialize(table.filepath)
        report = build_cleaning_preview(
            local_path,
            max_rows=int(current_app.config["CLEANING_MAX_ROWS"]),
        )
        report["fingerprint"] = storage.fingerprint(table.filepath)
    except CleaningLimitExceeded as exc:
        return jsonify({"success": False, "error": str(exc)}), 413
    except Exception as exc:
        return jsonify({"success": False, "error": f"Could not profile CSV: {exc}"}), 400

    token = None
    if report["actions"]:
        token = str(uuid.uuid4())
        previews = dict(session.get("cleaning_previews", {}))
        previews = {
            key: value
            for key, value in previews.items()
            if value.get("table_id") != table.id
        }
        previews = dict(list(previews.items())[-4:])
        previews[token] = {
            "table_id": table.id,
            "fingerprint": report["fingerprint"],
            "actions": [action["id"] for action in report["actions"]],
        }
        session["cleaning_previews"] = previews
        session.modified = True

    public_report = dict(report)
    public_report.pop("fingerprint", None)
    return jsonify({
        "success": True,
        "table": {"id": table.id, "name": table.name},
        "requires_cleaning": bool(report["actions"]),
        "approval_token": token,
        "report": public_report,
    })


@tables_bp.route("/api/tables/<int:id>/clean/apply", methods=["POST"])
def apply_table_cleaning(id):
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    table = get_table_if_owner(id, user_id)
    if not table:
        return jsonify({"success": False, "error": "Table not found"}), 404

    body = request.get_json(silent=True) or {}
    token = body.get("approval_token")
    if body.get("approved") is not True or not token:
        return jsonify({
            "success": False,
            "error": "Explicit approval and a preview token are required.",
        }), 400

    previews = dict(session.get("cleaning_previews", {}))
    plan = previews.get(str(token))
    if not plan or plan.get("table_id") != table.id:
        return jsonify({"success": False, "error": "Cleaning preview expired."}), 409
    storage = get_dataset_storage()
    try:
        current_fingerprint = storage.fingerprint(table.filepath)
    except DatasetStorageError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if current_fingerprint != plan.get("fingerprint"):
        previews.pop(str(token), None)
        session["cleaning_previews"] = previews
        return jsonify({
            "success": False,
            "error": "The source file changed. Generate a new cleaning preview.",
        }), 409

    table_name, filename = _unique_cleaned_names(table)
    cache_dir = current_app.config.get(
        "DATASET_CACHE_DIR",
        current_app.config["UPLOAD_FOLDER"],
    )
    os.makedirs(cache_dir, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(
        prefix="aistora-clean-",
        suffix=".csv",
        dir=cache_dir,
        delete=False,
    )
    temp_path = temp_handle.name
    temp_handle.close()
    started = time.monotonic()
    committed = False
    stored_reference = None

    try:
        source_path = storage.materialize(table.filepath)
        cleaning_result = write_cleaned_copy(
            source_path,
            temp_path,
            plan["actions"],
            max_rows=int(current_app.config["CLEANING_MAX_ROWS"]),
        )
        dataframe = DataFrame(temp_path)
        stored_reference = storage.put_file(
            temp_path,
            table.project_id,
            filename,
        )
        cleaned_table = Table(
            name=table_name,
            filename=filename,
            filepath=stored_reference,
            columns_schema=dataframe.get_column_types(),
            row_count=len(dataframe),
            project_id=table.project_id,
        )
        db.session.add(cleaned_table)
        db.session.commit()
        committed = True

        previews.pop(str(token), None)
        session["cleaning_previews"] = previews
        schema = build_project_schema(table.project_id)

        duration_ms = round((time.monotonic() - started) * 1000)
        AgentAuditLogger().record(
            str(token),
            user_id,
            "auto_clean",
            {"source": table.name, "save_as": table_name},
            "ok",
            duration_ms,
        )
        return jsonify({
            "success": True,
            "message": "Created a cleaned copy. The original table was not changed.",
            "source_table": table.name,
            "cleaned_table": table_name,
            "actions": plan["actions"],
            "input_rows": cleaning_result["input_rows"],
            "output_rows": cleaning_result["output_rows"],
            "schema": schema,
        })
    except CleaningLimitExceeded as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 413
    except DatasetStorageError as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 500
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": f"Cleaning failed: {exc}"}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if not committed and stored_reference:
            try:
                storage.delete(stored_reference)
            except DatasetStorageError:
                pass
