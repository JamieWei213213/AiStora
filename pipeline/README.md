# pipeline/

The data platform behind AIStora. See `docs/DATA_PLATFORM.md` for the design;
this file is the map.

| Module | Role |
|---|---|
| `config.py` | `PipelineSettings` from the environment (`PIPELINE_BACKEND=local|aws`) |
| `keys.py` | Every prefix and key in the lake, in one place |
| `objectstore.py` | put/get/list/lock over a directory or an S3 bucket |
| `manifest.py` | The per-load ledger document the stages share |
| `validate.py` | Stage 1: size, encoding, delimiter, header sanitising, ragged rows |
| `contract.py` | Stage 2: full-file type inference, dataset contracts, evolution rules |
| `quality.py` | Stage 3: the quality gate (fail / warn / info checks) |
| `transform.py` | Stage 4: typed Parquet with `__load_id`, `__row_hash`, `__loaded_at` |
| `lakehouse.py` | Stage 5: Iceberg tables (replace / append / merge, history, snapshots, rollback) |
| `stages.py` | The six stage functions and the failure handler |
| `local_runner.py` | Runs the stages in-process (compose, tests, CLI) |
| `lambda_handler.py` | Single Lambda entry point (stages + jobs) |
| `local_scheduler.py` | Stands in for EventBridge Scheduler under docker-compose |
| `events.py` | Telemetry sink: batched JSONL to `events/` |
| `telemetry_job.py` | Nightly dbt build of the gold marts |
| `compaction.py` | Nightly small-file compaction + snapshot expiry |
| `metrics.py` | Read side: live numbers from manifests, daily series from gold |
| `connectors/` | Postgres (watermark) and Google Sheets (snapshot) sources |
| `dagster_defs.py` | Optional Dagster assets over the same functions |
| `dbt/` | The dbt project (DuckDB adapter) |

Run a file through the pipeline locally:

```
LAKE_ROOT=instance/lake python -m pipeline.local_runner data.csv --project 1 --dataset invoices --mode merge --key invoice_id
```
