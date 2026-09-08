"""Project schema access.

The schema used to be copied into the user's session and re-synced by hand from
four different call sites (upload, database selection, cleaning apply, and the
schema endpoint). That made the session the second source of truth for data
that already lives in Postgres, and it was the single largest contributor to
session size. It is now read from the database on demand.
"""

from flask import session

from models import Table


def build_project_schema(project_id):
    """Return {table_name: {id, filename, types, row_count}} for a project."""
    schema = {}
    for table in Table.query.filter_by(project_id=project_id).all():
        schema[table.name] = {
            "id": table.id,
            "filename": table.filename,
            "types": table.columns_schema or {},
            "row_count": table.row_count,
        }
    return schema


def active_schema():
    """Schema of the caller's currently selected project, or {} if none."""
    project_id = session.get("active_project_id")
    if not project_id:
        return {}
    return build_project_schema(project_id)
