#!/bin/sh
set -eu

python -c "from app import app, db; app.app_context().push(); db.create_all()"
exec gunicorn -c gunicorn_config.py app:app
