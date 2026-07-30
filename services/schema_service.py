from models import Table


def build_project_schema(project_id):
    schema = {}
    for table in Table.query.filter_by(project_id=project_id).all():
        schema[table.name] = {
            "id": table.id,
            "filename": table.filename,
            "types": table.columns_schema,
            "row_count": table.row_count,
        }
    return schema
