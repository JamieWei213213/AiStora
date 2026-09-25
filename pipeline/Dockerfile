# Pipeline Lambda image: every stage, the nightly jobs and the connectors.
#
# Built from the repository root so the image carries `pipeline/` and the
# `engine/` package it shares with the app:
#
#     docker build -f pipeline/Dockerfile -t aistora-pipeline .
#
# The handler is pipeline.lambda_handler.handler; the event decides what runs.
FROM public.ecr.aws/lambda/python:3.12

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIPELINE_BACKEND=aws \
    ICEBERG_CATALOG=glue \
    PIPELINE_SCRATCH_DIR=/tmp/aistora-pipeline \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false \
    DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb-extensions \
    HOME=/tmp

COPY pipeline/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

# Only what the stages import. No Flask, no templates, no tests.
COPY pipeline/ ${LAMBDA_TASK_ROOT}/pipeline/
COPY engine/ ${LAMBDA_TASK_ROOT}/engine/

# DuckDB fetches extensions on first use into a per-user directory; /tmp is a
# fresh mount on every Lambda cold start, so httpfs is installed into a
# read-only directory inside the image instead and pointed at through
# DUCKDB_EXTENSION_DIRECTORY (pipeline/duck.py sets it on every connection).
RUN mkdir -p /opt/duckdb-extensions /tmp/aistora-pipeline \
    && python -c "import duckdb; c = duckdb.connect(); c.execute(\"SET extension_directory = '/opt/duckdb-extensions'\"); c.execute('INSTALL httpfs'); c.execute('LOAD httpfs'); c.close()" \
    && chmod -R a+rX /opt/duckdb-extensions

CMD ["pipeline.lambda_handler.handler"]
