# routes/data.py
import os
import tempfile

from flask import Blueprint, request, jsonify, session, current_app
from werkzeug.utils import secure_filename

from extensions import db
from models import Table, Project
from engine.dataframe import DataFrame
from engine.parser import CsvParseError
from services.schema_service import active_schema, build_project_schema
from services.storage_service import DatasetStorageError, get_dataset_storage
from services.validation import ValidationError, validate_column_names

data_bp = Blueprint('data', __name__)

@data_bp.route('/api/upload', methods=['POST'])
def upload_files():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized. Please log in.'}), 401
        
    active_project_id = session.get('active_project_id')
    if not active_project_id:
        return jsonify({'success': False, 'error': 'No database selected'}), 400
    project = Project.query.filter_by(
        id=active_project_id,
        user_id=session["user_id"],
    ).first()
    if not project:
        return jsonify({"success": False, "error": "Database not found"}), 404

    if 'files' not in request.files:
        return jsonify({'success': False, 'error': 'No files part'}), 400

    files = request.files.getlist('files')
    # Names are checked against the database, not against a session copy.
    schema_cache = build_project_schema(active_project_id)
    max_tables = int(current_app.config.get("MAX_TABLES_PER_PROJECT", 20) or 0)
    if max_tables and len(schema_cache) + len(files) > max_tables:
        return jsonify({
            "success": False,
            "error": f"A database may hold at most {max_tables} tables.",
            "error_type": "input_limit",
        }), 400
    storage = get_dataset_storage()
    stored_references = []
    temporary_paths = []

    try:
        for file in files:
            filename = secure_filename(file.filename or "")
            if not filename or os.path.splitext(filename)[1].lower() != ".csv":
                raise DatasetStorageError(
                    "Each uploaded dataset must be a CSV file with a valid filename."
                )

            temp_handle = tempfile.NamedTemporaryFile(
                prefix="aistora-upload-",
                suffix=".csv",
                dir=current_app.config.get(
                    "DATASET_CACHE_DIR",
                    current_app.config["UPLOAD_FOLDER"],
                ),
                delete=False,
            )
            temp_path = temp_handle.name
            temp_handle.close()
            temporary_paths.append(temp_path)
            file.save(temp_path)

            df = DataFrame(source=temp_path)
            column_types = df.get_column_types()
            validate_column_names(
                column_types,
                max_columns=int(current_app.config.get("MAX_UPLOAD_COLUMNS", 200) or 0),
                max_name_chars=int(current_app.config.get("MAX_COLUMN_NAME_CHARS", 64) or 0),
            )
            row_count = len(df)
            table_base = secure_filename(os.path.splitext(filename)[0]) or "table"
            table_name = table_base
            suffix = 2
            while (
                table_name in schema_cache
                or Table.query.filter_by(
                    project_id=active_project_id,
                    name=table_name,
                ).first()
            ):
                table_name = f"{table_base}_{suffix}"
                suffix += 1

            storage_reference = storage.put_file(
                temp_path,
                active_project_id,
                filename,
            )
            stored_references.append(storage_reference)
            new_table = Table(
                name=table_name,
                filename=filename,
                filepath=storage_reference,
                columns_schema=column_types,
                row_count=row_count,
                project_id=active_project_id
            )
            db.session.add(new_table)
            db.session.flush()

            schema_cache[table_name] = {
                'id': new_table.id,
                'filename': filename,
                'types': column_types,
                'row_count': row_count
            }

        db.session.commit()
    except (DatasetStorageError, ValidationError, CsvParseError) as exc:
        db.session.rollback()
        for reference in stored_references:
            try:
                storage.delete(reference)
            except DatasetStorageError:
                pass
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        for reference in stored_references:
            try:
                storage.delete(reference)
            except DatasetStorageError:
                pass
        return jsonify({
            "success": False,
            "error": f"Dataset upload failed: {str(exc)[:240]}",
        }), 500
    finally:
        for temp_path in temporary_paths:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    return jsonify({'success': True, 'schema': build_project_schema(active_project_id)})


@data_bp.route("/api/schema", methods=["GET"])
def get_schema():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    project_id = session.get("active_project_id")
    if not project_id:
        return jsonify({"success": False, "error": "No database selected"}), 400
    project = Project.query.filter_by(id=project_id, user_id=session["user_id"]).first()
    if not project:
        return jsonify({"success": False, "error": "Database not found"}), 404
    return jsonify({"success": True, "schema": build_project_schema(project_id)})
