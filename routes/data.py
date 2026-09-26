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
    """Upload CSVs into the active database.

    With the data pipeline enabled (the default) this is the same as
    ``POST /api/loads`` in replace mode: the file lands in the lake, is
    validated, typed, quality-gated and versioned, and the table points at
    the curated Parquet. The pre-pipeline path below is kept for
    ``PIPELINE_ENABLED=false`` and for the tests that pin its behaviour.
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized. Please log in.'}), 401
    if current_app.config.get("PIPELINE_ENABLED"):
        from routes.loads import create_loads

        return create_loads()
    return _legacy_upload()


def _legacy_upload():

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
    cache_dir = current_app.config.get("DATASET_CACHE_DIR", current_app.config["UPLOAD_FOLDER"])
    temporary_paths = []
    sources = []
    try:
        for file in files:
            filename = secure_filename(file.filename or "")
            temp_handle = tempfile.NamedTemporaryFile(
                prefix="aistora-upload-", suffix=".csv", dir=cache_dir, delete=False,
            )
            temp_path = temp_handle.name
            temp_handle.close()
            temporary_paths.append(temp_path)
            file.save(temp_path)
            sources.append((filename, temp_path))
        return ingest_csv_sources(active_project_id, sources)
    finally:
        for temp_path in temporary_paths:
            if os.path.exists(temp_path):
                os.remove(temp_path)


def ingest_csv_sources(project_id, sources):
    """Register CSV files on disk as tables of ``project_id``.

    ``sources`` is a list of ``(filename, local_path)``. Shared by the upload
    route and the sample-dataset loader so both paths validate, type, store
    and warn identically. Returns a Flask response tuple/JSON.
    """
    schema_cache = build_project_schema(project_id)
    max_tables = int(current_app.config.get("MAX_TABLES_PER_PROJECT", 20) or 0)
    if max_tables and len(schema_cache) + len(sources) > max_tables:
        return jsonify({
            "success": False,
            "error": (
                f"A database may hold at most {max_tables} tables. Delete a table "
                "or create another database, then upload again."
            ),
            "error_type": "input_limit",
        }), 400
    storage = get_dataset_storage()
    stored_references = []
    warnings = []

    try:
        for filename, local_path in sources:
            filename = secure_filename(filename or "")
            if not filename or os.path.splitext(filename)[1].lower() != ".csv":
                raise DatasetStorageError(
                    "Each uploaded dataset must be a CSV file with a valid filename."
                )

            df = DataFrame(source=local_path)
            column_types = df.get_column_types()
            validate_column_names(
                column_types,
                max_columns=int(current_app.config.get("MAX_UPLOAD_COLUMNS", 200) or 0),
                max_name_chars=int(current_app.config.get("MAX_COLUMN_NAME_CHARS", 64) or 0),
            )
            row_count = len(df)
            warnings.extend(_upload_warnings(filename, df, row_count))
            table_base = secure_filename(os.path.splitext(filename)[0]) or "table"
            table_name = table_base
            suffix = 2
            while (
                table_name in schema_cache
                or Table.query.filter_by(
                    project_id=project_id,
                    name=table_name,
                ).first()
            ):
                table_name = f"{table_base}_{suffix}"
                suffix += 1

            storage_reference = storage.put_file(
                local_path,
                project_id,
                filename,
            )
            stored_references.append(storage_reference)
            new_table = Table(
                name=table_name,
                filename=filename,
                filepath=storage_reference,
                columns_schema=column_types,
                row_count=row_count,
                project_id=project_id
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

    return jsonify({
        'success': True,
        'schema': build_project_schema(project_id),
        'warnings': warnings,
    })


def _upload_warnings(filename, df, row_count):
    """Things the uploader should know that are not errors.

    A silently skipped row or a zero-row table used to be visible only in the
    server log; the person uploading saw "success" and a smaller row count
    than their spreadsheet.
    """
    notes = []
    parser = getattr(df, "parser", None)
    skipped = int(getattr(parser, "skipped_rows", 0) or 0)
    if skipped:
        notes.append(
            f"{filename}: {skipped} row{'s' if skipped != 1 else ''} skipped because "
            "the number of cells did not match the header. Use Auto clean to pad "
            "or trim those rows, or fix the file and upload it again."
        )
    if row_count == 0:
        notes.append(f"{filename}: the file has a header but no data rows.")
    separator = getattr(parser, "separator", ",")
    if separator and separator != ",":
        label = {";": "semicolons", "\t": "tabs", "|": "pipes"}.get(separator, repr(separator))
        notes.append(f"{filename}: read as a {label}-separated file.")
    return notes


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
