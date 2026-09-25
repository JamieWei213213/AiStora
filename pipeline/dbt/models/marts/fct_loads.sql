{{ config(materialized='external', location=lake_path('gold/fct_loads.parquet')) }}

{#- One row per load, straight from the manifests, with a derived
    throughput figure. The load id is a ULID so ordering by it is
    chronological. -#}
select
    load_id,
    project_id,
    dataset,
    mode,
    source,
    status,
    last_stage,
    created_at,
    finished_at,
    dt,
    raw_bytes,
    duration_ms,
    rows_in,
    rows_out,
    rows_rejected,
    rows_inserted,
    rows_updated,
    rows_skipped,
    column_count,
    quality_status,
    duplicate_rows,
    error_type,
    snapshot_id,
    case when duration_ms > 0 then rows_out * 1000.0 / duration_ms end as rows_per_second
from {{ ref('stg_loads') }}
where load_id is not null
