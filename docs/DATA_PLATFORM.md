# The AIStora data platform

*Added September 2026. This document explains the data engineering layer:
what problem each piece solves, the decisions behind it, and its honest
limits. `pipeline/README.md` is the module map; `infra/terraform/pipeline/`
deploys it.*

## Contents

- [Why a pipeline](#why-a-pipeline)
- [The lake](#the-lake)
- [A load, stage by stage](#a-load-stage-by-stage)
- [Recurring loads: replace, append, merge](#recurring-loads-replace-append-merge)
- [Schema contracts and evolution](#schema-contracts-and-evolution)
- [The quality gate](#the-quality-gate)
- [Orchestration](#orchestration)
- [Telemetry and the gold marts](#telemetry-and-the-gold-marts)
- [Connectors](#connectors)
- [Maintenance](#maintenance)
- [The app's side](#the-apps-side)
- [Running it](#running-it)
- [Cost](#cost)
- [Decisions and the alternatives not taken](#decisions-and-the-alternatives-not-taken)
- [Limits](#limits)

## Why a pipeline

Before this layer, an upload was one HTTP request: save the file, parse it in
the request thread, infer types from a 1,000-row sample, insert a `Table` row,
answer. It worked, and it had four problems that a real accounting team hits
in the first month:

1. **Every upload was a new table.** The same client's QuickBooks export
   arrives every month. There was no way to say "this is the September file
   for the *invoices* dataset; update the invoices that changed and add the
   new ones."
2. **Nothing was versioned.** A bad file replaced nothing (it made a new
   table), but a cleaned copy or a re-upload could not be compared with, or
   rolled back to, what was there before.
3. **Validation was best-effort and sampled.** A column that was numeric for
   its first thousand rows and text at row 5,000 was typed wrong; a ragged
   row was skipped with a log line nobody read; `$1,200.50` was text.
4. **The audit log went to `/tmp`** and was gone at the next deploy, so
   there was no durable record of what the agent or the pipeline did.

The pipeline is the answer to all four, built as a small **lakehouse**: every
file lands immutably, is validated and typed against a per-dataset contract,
passes a quality gate, is written as Parquet, and is merged into an Apache
Iceberg table that carries the dataset's whole history. The app reads a
Parquet materialisation of the current snapshot, so the agent, the EDA report
and the schema endpoint see a pipeline-loaded dataset exactly like any other
table.

The same code runs in three places: the AWS Lambda image (orchestrated by
Step Functions), the local runner used by docker-compose and the tests, and,
optionally, Dagster. The stages are pure functions of a manifest; the
orchestrator adds retries and visibility, never logic.

## The lake

One bucket (or one directory locally), one key layout, defined once in
`pipeline/keys.py` so Terraform lifecycle rules, IAM prefixes, EventBridge
filters and the code agree:

```
raw/project=<id>/dataset=<slug>/load_id=<ulid>/<file>.csv   as uploaded, immutable
work/…/load_id=<ulid>/working.csv                           UTF-8 copy between stages (1-day lifecycle)
silver/…/load_id=<ulid>/part-0.parquet                      typed, validated (60-day lifecycle)
iceberg/<namespace>/<table>/…                               Iceberg data + metadata, PyIceberg-managed
curated/project=<id>/dataset=<slug>/current/part-0.parquet  the current snapshot, what the app reads
quarantine/…/load_id=<ulid>/{<file>, report.json}           rejected files with the reason (30 days)
contracts/project=<id>/dataset=<slug>/contract.json         the dataset's schema promise
manifests/project=<id>/dataset=<slug>/<ulid>.json           one ledger document per load
locks/project=<id>/dataset=<slug>/curate.lock               one writer per dataset
events/dt=YYYY-MM-DD/hour=HH/<host>-<ulid>.jsonl.gz         telemetry
gold/<mart>.parquet                                         nightly dbt marts
connectors/<id>/{config.json, state.json}                   scheduled sources
```

Load ids are ULIDs: they sort by creation time, so a listing of manifests is
already in load order, and an id doubles as a timestamp when debugging.

## A load, stage by stage

```
upload / connector / S3 drop
        │
        ▼
   raw/ object ──(EventBridge)──▶ Step Functions ──▶ validate ─▶ profile ─▶ gate ─▶ transform ─▶ curate ─▶ register
                                                        │           │         │                    │
                                                        ▼           ▼         ▼                    ▼
                                                    quarantine  quarantine quarantine        Iceberg table
                                                    (bad file)  (schema    (quality           + curated/
                                                                conflict)  failure)            + snapshot
```

Every stage loads the manifest for `(project_id, dataset, load_id)`, does its
work against what is in the lake, writes the manifest back and returns a
small payload whose `status` tells the orchestrator whether to continue.
Re-running a stage after a crash overwrites the same keys and produces the
same manifest.

**validate** (`pipeline/validate.py`). Size (≤ 500 MB by default), extension,
encoding (UTF-8 with or without BOM, Windows-1252, Latin-1, UTF-16),
delimiter sniffing (`, ; tab |`), and the header. Column names are reduced to
`[A-Za-z0-9_ ]{1,64}` with the rename recorded in the manifest, because a
header is an untrusted string that reaches the model in every prompt and the
DOM in every schema drawer. A UTF-8 working copy is written with the clean
header; ragged rows are counted (and sampled for the report), never padded
or truncated silently.

**profile** (`pipeline/contract.py`). DuckDB reads the whole working copy as
text and one aggregate query per file counts, per column, how many values fit
each type: `bool`, `int`, `float`, `float` with a *currency* transform
(`$1,200.50`, `(20.00)`), ISO `date`, US `date` (`01/05/2024`),
`timestamp`, else `str`. Types are chosen narrowest-first on an exact match;
if nothing matches exactly, a type is accepted when at least 95% of values
fit, with `int` excluded from that pass so a column that is 97% integers and
3% decimals becomes `float`, never `int` with 3% nulls. Columns that are
unique and never null are offered as candidate merge keys. The result is
diffed against the stored contract (see below).

**gate** (`pipeline/quality.py`). Checks run against the *typed* view that
would be loaded: row count, ragged rows, per-column cast-failure share
against the threshold, empty columns, merge-key nullability and uniqueness,
exact duplicate rows. `fail` quarantines; `warn` loads with the flag on the
manifest and in the UI; `info` is a number worth keeping.

**transform** (`pipeline/transform.py`). One zstd Parquet file with the
contract's types plus three bookkeeping columns the app hides:
`__load_id`, `__row_hash` (sha256 over the typed values, for de-duplicating
appends) and `__loaded_at`.

**curate** (`pipeline/lakehouse.py`). The silver file is merged into the
dataset's Iceberg table according to the load mode, under a per-dataset lock,
and the current snapshot is materialised to `curated/…/current/part-0.parquet`.

**register**. The evolved contract is persisted, work files are deleted, and
the manifest is marked `succeeded`. The app's next sync creates or updates
the `Table` row.

**on_failure**. Anything unexpected (a Lambda timeout, a bug) lands in the
manifest as `failed` with the error; the raw file stays where it is. A
quarantine is a *correct* outcome and a Succeed state; a failure is a Fail
state and pages.

## Recurring loads: replace, append, merge

Each dataset has a load mode chosen per upload (or per connector):

| Mode | Semantics | Iceberg operation |
|---|---|---|
| `replace` | The table becomes this file. Also the only mode allowed to reset a conflicting schema. | `overwrite` |
| `append` | Add rows whose `__row_hash` is not already present; exact duplicates are skipped and counted. | `append` |
| `merge` | Rows are matched on the declared key columns; changed rows are replaced, new rows inserted, unchanged rows skipped. Optional type-2 history. | `delete` + `append` in one transaction |

Every load is an Iceberg snapshot tagged with its load id and mode. The UI
lists snapshots, and **rollback** restores the snapshot that preceded the most
recent load and re-materialises the curated file. History, when enabled, is
an append-only `<table>__history` table of row versions with `__valid_from`
and `__change` (`insert` / `update`); `valid_to` is the next version's
`__valid_from`, computed at read time, which keeps the write path a plain
append.

Merge classification (which incoming rows are inserts, updates or unchanged)
is done in DuckDB by joining the incoming batch against the table's keys and
hashes, and the update is a delete-by-key plus append inside one PyIceberg
transaction. PyIceberg 0.12's own `upsert` was tried first and dropped: it
reads matched rows with each data file's *own* schema, so it breaks on the
first merge after a column was added.

## Schema contracts and evolution

The first load of a dataset infers a **contract** from the whole file and
stores it. Every later load is diffed against it and judged by explicit
rules, because "the schema changed" is the most common way a recurring load
silently corrupts a table:

| Change | Decision |
|---|---|
| New column | Allowed. Added as nullable to the contract and the Iceberg table. |
| Missing column | Allowed with a warning. Loaded as null. |
| Incoming type narrower than stored (ints in a float column, anything into a text column, date into timestamp) | Allowed. Values are cast to the stored type. |
| Incoming type wider than stored (text in an int column, timestamps in a date column) | **Blocked.** The load is quarantined with a message naming the columns. |
| Merge key missing from the file | **Blocked.** |

The escape hatch is deliberate and documented in the message: reload the
dataset in `replace` mode, which resets the contract and recreates the table.
Iceberg only promotes `int→long` and `float→double` in place, so pretending
to widen a column would either lose data or lie about the table.

## The quality gate

| Check | Severity | Rule |
|---|---|---|
| `row_count` | fail | at least `PIPELINE_MIN_ROWS` data rows (default 1) |
| `malformed_rows` | fail / warn | ragged rows above / below the failure threshold |
| `cast_failures` | fail / warn | any typed column with more than `PIPELINE_CAST_FAILURE_THRESHOLD` (5%) of values that do not cast → fail; fewer → warn and load as null |
| `key_not_null`, `key_unique` | fail | merge keys must identify rows |
| `empty_columns` | warn | columns with no values at all |
| `duplicate_rows` | info | exact duplicates after typing |

Great Expectations and Soda were considered and not used: the schemas here
are user-defined at upload time, so the checks that matter are relative to
the inferred contract, and a dozen DuckDB queries are easier to read, test
and extend than a framework's DSL. dbt tests cover the gold layer.

## Orchestration

On AWS: an S3 `Object Created` event under `raw/` reaches EventBridge, which
starts one Step Functions Standard execution per file. Each state invokes the
same Lambda with `{"stage": …, "payload": …}`; Choice states route on the
returned `status`. Retries: a `RetryableError` (another load holds the
dataset lock) is retried with backoff for up to ~20 minutes; Lambda service
errors are retried briefly; anything else goes through `OnFailure` to a Fail
state. Execution history is the run log; the Terraform module adds alarms on
failed executions, Lambda errors, throttles, slow p95 and DLQ depth.

Locally: `pipeline/local_runner.py` runs the same six functions in sequence
on a background thread so the upload request returns at once and the UI polls
`GET /api/loads/<id>` exactly as it does on AWS. `PIPELINE_SYNC_WAIT_SECONDS`
lets small files finish before the response so the schema is already there.

Why not Airflow, Dagster or Prefect as *the* orchestrator: each needs a
server (scheduler, web, database, workers) that would either share the demo
box's 2 GB with the app or cost $25+/month on its own, and at one execution
per file they would add a dependency record and nothing else. Step Functions
gives retries, a visual DAG and history for free at this volume.
`pipeline/dagster_defs.py` wraps the same functions as Dagster assets for
anyone who wants that UI locally; it is optional and nothing imports it.

## Telemetry and the gold marts

The app emits structured events (`agent.run`, `agent.tool_call`,
`pipeline.received`, `pipeline.stage`, `pipeline.load`, `pipeline.rollback`,
`pipeline.connector`, …) through `pipeline/events.py`: metadata only, never
a cell value or the question text, batched and flushed as gzip JSONL to
`events/`. A failed flush is logged and the batch dropped; telemetry is
never load-bearing.

Nightly, `pipeline/telemetry_job.py` runs the dbt project in `pipeline/dbt/`
with the DuckDB adapter. Staging models read the events and the manifests
straight from the lake (explicit column lists, so a field no file carries yet
is a typed null rather than an error, and an empty lake yields empty
relations rather than a failed run). Marts are written as Parquet under
`gold/`:

| Mart | Grain |
|---|---|
| `fct_loads` | one row per load, with rows/second |
| `fct_agent_runs` | one row per agent run: tier, tokens, cost, latency, verification |
| `agg_daily_pipeline` | per day and project: loads by outcome, quarantine rate, rows, p95 duration |
| `agg_daily_agent` | per day and project: runs, success and verification rates, tokens, cost, p95 latency |
| `agg_dataset_health` | per dataset: loads by outcome, last successful load, rows, quality |

dbt tests (`unique`, `not_null`, `accepted_values`, unique combinations,
bounds) run in the same build; a failed test fails the job and alarms. The
project has no package dependencies on purpose — a Lambda that runs
`dbt deps` against the network at 03:00 is a failure waiting to happen — so
the two generic tests it needs are defined in `macros/tests.sql`.

`GET /api/pipeline/metrics` returns two things: **live** numbers computed
from the manifests on request (always available) and the **gold** daily
series when the marts exist. dbt-core 1.x is used rather than the new
Rust-based dbt v2 (GA 14 September 2026): the project parses under the v2
parser, so switching is a Dockerfile change once v2 has run in Lambda images
for a while.

## Connectors

A connector is a producer, nothing more: on its schedule it extracts, lands a
CSV in `raw/` with a manifest, and the same six stages take over.

- **Postgres** (`pipeline/connectors/postgres_source.py`): high-watermark
  extraction on a cursor column (`WHERE cursor > :last ORDER BY cursor LIMIT
  n`) with the cursor stored in the lake; without a cursor column every run
  is a full extract for `replace` mode. Identifiers are validated, values are
  parameterised, the server-side cursor streams.
- **Google Sheets** (`pipeline/connectors/google_sheets.py`): a snapshot of a
  range via the Sheets API with a service-account key; `replace`, or `merge`
  with keys when rows are edited in place.

Configs live in `connectors/<id>/config.json` and are managed through
`/api/connectors`; secrets are never stored there — `secret_ref` names an
SSM `SecureString` (AWS) or an environment variable (local). EventBridge
Scheduler invokes the Lambda with `{"job": "connector", "connector_id": …}`;
the app's **Run now** does the same through `lambda:InvokeFunction`.

QuickBooks Online is the connector that fits the product story best and is
deferred: it needs an Intuit developer app and an OAuth2 refresh-token flow,
which is a week of work that adds no new pipeline concept.

## Maintenance

`pipeline/compaction.py` runs nightly: any table whose data-file count passed
`PIPELINE_COMPACTION_MIN_FILES` (8) is rewritten as one snapshot with one
file per ~128 MB, and snapshots older than `PIPELINE_SNAPSHOT_RETENTION_DAYS`
(30) are expired so the rewritten files can go. PyIceberg has no
`rewrite_data_files`; `overwrite` of the full scan is the honest equivalent
at these volumes, and it is a normal snapshot, so a compaction is as
reversible as a load. S3 lifecycle rules handle `work/`, `quarantine/`,
`silver/` and `raw/` (Glacier IR after 90 days).

## The app's side

- `services/load_service.py` is the bridge. `start_upload` lands the file
  with its manifest and records a `Load` row; `sync_load` copies a manifest's
  state into that row and, on success, creates or updates the `Table` that
  points at the curated Parquet; `rollback_load` restores the previous
  snapshot; `sync_project_loads` picks up loads the app did not start
  (connectors, files dropped straight into `raw/`).
- `engine/parquet_source.py` gives the engine a Parquet source with the same
  three-method surface as the CSV parser. Types come from the file's schema
  (exact, not sampled) and `project()` reads only the requested columns, which
  is what lifts the practical row ceiling: a filter on three columns of a
  five-million-row file reads three columns.
- `services/storage_service.py` learned to read the lake's `curated/` and
  `silver/` prefixes (and never to delete there).
- `POST /api/upload` delegates to `POST /api/loads` when
  `PIPELINE_ENABLED` is on; the legacy synchronous path remains behind the
  flag. Auto-clean refuses pipeline-managed tables (they were normalised at
  load time, and editing the curated file in place would bypass the snapshot
  history).
- The upload screen gained load options (mode, keys, dataset, history), a
  **Data loads** list with the six-step progress strip, quality report,
  schema changes, snapshots and rollback, and a **Pipeline health** panel.

The pipeline never connects to the app's database: manifests in the lake are
the source of truth, and the app syncs from them. That is what lets the
pipeline run while the app stack is destroyed.

## Running it

Local, in-process (the default with `docker compose up`; the `scheduler`
service stands in for EventBridge Scheduler and runs the nightly jobs at
03:00 UTC and connectors on their `rate(...)` schedules):

```bash
PIPELINE_ENABLED=true PIPELINE_BACKEND=local LAKE_ROOT=instance/lake
python -m pipeline.local_runner data.csv --project 1 --dataset invoices --mode merge --key invoice_id
```

AWS: apply `infra/terraform/pipeline/`, build and push the image with the
**Pipeline** workflow, then point the app stack at the lake
(`lake_bucket_name`, `pipeline_function_name`, `glue_database_name`). The
deploy job's smoke test drops a file in `raw/project=0/…` and waits for its
manifest to say `succeeded`.

Optional Dagster UI:

```bash
pip install dagster dagster-webserver
dagster dev -m pipeline.dagster_defs
```

## Cost

Idle, on AWS, the whole pipeline module is about **$1–2 a month**: S3
pennies, everything else (Lambda, Step Functions, EventBridge, Glue catalog,
SQS, SNS, logs) inside free tiers at demo volume, plus five CloudWatch
alarms at $0.10 each. Nothing runs continuously: no NAT gateway, no VPC
endpoints, no orchestrator server. The full table is in
`infra/terraform/pipeline/README.md`. A 30-second load on a 1,769 MB Lambda
costs about a tenth of a cent.

## Decisions and the alternatives not taken

| Decision | Alternative | Why |
|---|---|---|
| Step Functions + Lambda | Airflow / Dagster / Prefect server | No server to run or pay for; one execution per file needs retries and a DAG view, not a scheduler. Dagster wrapper kept for local use. |
| Self-managed Iceberg with PyIceberg + Glue catalog | S3 Tables | S3 Tables' managed compaction bills $0.05/GB processed plus per-object monitoring — 20–30× doing it yourself — with little visibility. |
| Iceberg at all | Plain Parquet, copy-on-write | Merge semantics, time travel and rollback are the product requirement; Parquet alone gives none of them. |
| DuckDB in Lambda | Athena / Glue ETL | Faster than Athena under ~20 GB and free; Glue crawlers bill 10-minute minimums. Athena still works on the catalog for ad-hoc SQL. |
| Contract + DuckDB checks | Great Expectations / Soda | Schemas are user-defined at load time; checks relative to an inferred contract are simpler as SQL. |
| dbt-core 1.x + DuckDB | dbt v2 (Rust) | v2 is weeks old; the project is v2-parser clean so the switch is a Dockerfile change. |
| Manifests in the lake as the ledger | Pipeline writes to the app's Postgres | Keeps the Lambda out of the VPC (no NAT), and the pipeline keeps working while the app is down. |
| Delete + append in one transaction for merge | PyIceberg `upsert` | `upsert` breaks after schema evolution in 0.12. |

## Limits

- PyIceberg writes are copy-on-write and single-writer: loads for one
  dataset are serialised by the lock and the state machine's retry, which
  is right for monthly exports and wrong for a firehose.
- Tables are unpartitioned. At a few hundred MB per file a partition spec
  would only add small files; at tens of GB it would be the first change.
- Append de-duplication reads the table's existing hashes into memory. Fine
  to a few million rows; beyond that it should become an anti-join in DuckDB
  over the Iceberg scan.
- A Lambda invocation has 15 minutes and 10 GB of `/tmp`, so raw files are
  capped at 500 MB. Larger files would route to a Fargate task the app stack
  already has; that path is documented, not built.
- History is append-only versions with `valid_to` derived at read time.
  Deletes in the source are not detected (a merge never removes rows).
- Type inference is deterministic and explainable, not clever: a column of
  US zip codes is `int`, and `06/01/2024` is read as 1 June. The contract is
  visible in the load report so a wrong guess is a `replace` reload away.
- The Google Sheets and Postgres connectors are exercised with fakes in the
  test suite; they are not exercised against live services in CI.
