# routes/databases.py
import os

from flask import current_app, Blueprint, request, jsonify, session
from extensions import db
from models import Project, Table
from services.schema_service import build_project_schema
from services.validation import ValidationError, validate_database_name
from services.storage_service import DatasetStorageError, get_dataset_storage

databases_bp = Blueprint('databases', __name__)

@databases_bp.route('/api/databases', methods=['GET'])
def get_databases():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    projects = Project.query.filter_by(user_id=session['user_id']).all()
    return jsonify({
        'success': True, 
        'databases': [{'id': p.id, 'name': p.name, 'table_count': len(p.tables)} for p in projects]
    })

@databases_bp.route('/api/databases', methods=['POST'])
def create_database():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    data = request.get_json(silent=True) or {}
    try:
        name = validate_database_name(data.get('name'))
    except ValidationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400

    max_projects = int(current_app.config.get('MAX_PROJECTS_PER_USER', 10) or 0)
    if max_projects and Project.query.filter_by(user_id=session['user_id']).count() >= max_projects:
        return jsonify({
            'success': False,
            'error': f'You may have at most {max_projects} databases. Delete one to add another.',
            'error_type': 'input_limit',
        }), 400

    new_project = Project(name=name, user_id=session['user_id'])
    db.session.add(new_project)
    db.session.commit()
    
    return jsonify({'success': True, 'database': {'id': new_project.id, 'name': new_project.name}})

@databases_bp.route('/api/databases/<int:id>', methods=['PUT'])
def rename_database(id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    project = Project.query.filter_by(id=id, user_id=session['user_id']).first()
    if not project:
        return jsonify({'success': False, 'error': 'Database not found'}), 404

    data = request.get_json(silent=True) or {}
    if 'name' in data:
        try:
            project.name = validate_database_name(data.get('name'))
        except ValidationError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
    db.session.commit()
    
    return jsonify({'success': True})

@databases_bp.route('/api/databases/<int:id>', methods=['DELETE'])
def delete_database(id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    project = Project.query.filter_by(id=id, user_id=session['user_id']).first()
    if not project:
        return jsonify({'success': False, 'error': 'Database not found'}), 404

    storage_references = [table.filepath for table in project.tables]
    db.session.delete(project)
    db.session.commit()

    cleanup_failures = 0
    storage = get_dataset_storage()
    for reference in storage_references:
        try:
            storage.delete(reference)
        except DatasetStorageError:
            cleanup_failures += 1

    return jsonify({
        "success": True,
        "storage_cleanup_failures": cleanup_failures,
    })

@databases_bp.route('/api/databases/select', methods=['POST'])
def select_database():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    data = request.get_json()
    project_id = data.get('id')
    
    project = Project.query.filter_by(id=project_id, user_id=session['user_id']).first()
    if not project:
        return jsonify({'success': False, 'error': 'Database not found'}), 404
        
    session['active_project_id'] = project.id
    # Switching projects must not carry the previous project's agent memory,
    # pending approvals or cleaning previews across.
    session.pop('db_relationships', None)
    session.pop('pending_agent', None)
    session.pop('cleaning_previews', None)

    schema = build_project_schema(project.id)
    return jsonify({'success': True, 'schema': schema, 'name': project.name})


SAMPLE_DATASET_NAME = "Sample: Coffee Shop"
SAMPLE_FILES = ("customers.csv", "orders.csv", "products.csv")


def _sample_dir():
    return os.path.join(current_app.static_folder, "samples", "coffee_shop")


@databases_bp.route('/api/databases/sample', methods=['POST'])
def create_sample_database():
    """Create a database pre-loaded with the bundled coffee-shop CSVs.

    First-run onboarding: a new account has nothing to analyse, and the
    fastest way to understand the product is to click one button and see
    three related tables, then ask a question. The files go through the
    same ingestion as an upload, so what the person sees is exactly what
    their own data would get.
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    from routes.data import ingest_csv_sources

    user_id = session['user_id']
    max_projects = int(current_app.config.get('MAX_PROJECTS_PER_USER', 10) or 0)
    if max_projects and Project.query.filter_by(user_id=user_id).count() >= max_projects:
        return jsonify({
            'success': False,
            'error': (
                f'You may have at most {max_projects} databases. Delete one to make '
                'room for the sample.'
            ),
            'error_type': 'input_limit',
        }), 400
    sources = []
    for filename in SAMPLE_FILES:
        path = os.path.join(_sample_dir(), filename)
        if not os.path.exists(path):
            return jsonify({'success': False, 'error': 'Sample data is not installed on this server.'}), 503
        sources.append((filename, path))

    name = SAMPLE_DATASET_NAME
    suffix = 2
    while Project.query.filter_by(user_id=user_id, name=name).first():
        name = f"{SAMPLE_DATASET_NAME} {suffix}"
        suffix += 1
    project = Project(name=name, user_id=user_id)
    db.session.add(project)
    db.session.commit()

    response = ingest_csv_sources(project.id, sources)
    status = response[1] if isinstance(response, tuple) else 200
    if status != 200:
        db.session.delete(project)
        db.session.commit()
        return response

    session['active_project_id'] = project.id
    session.pop('db_relationships', None)
    session.pop('pending_agent', None)
    session.pop('cleaning_previews', None)
    payload = response.get_json()
    payload['database'] = {'id': project.id, 'name': project.name}
    return jsonify(payload)
