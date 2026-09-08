#!/bin/sh
set -eu

# Alembic owns the schema. This replaces the previous db.create_all(), which
# could create missing tables but could never alter an existing one, so any
# model change needed manual SQL against the production database.
flask --app app db upgrade

exec gunicorn -c gunicorn_config.py app:app
