{{ config(materialized='external', location=lake_path('gold/agg_daily_pipeline.parquet')) }}

{#- Daily pipeline health per project: volume, outcome mix, latency. -#}
select
    dt,
    project_id,
    count(*)                                                        as loads,
    count(*) filter (where status = 'succeeded')                    as succeeded,
    count(*) filter (where status = 'quarantined')                  as quarantined,
    count(*) filter (where status = 'failed')                       as failed,
    round(count(*) filter (where status = 'quarantined') * 1.0 / count(*), 4) as quarantine_rate,
    coalesce(sum(rows_out), 0)                                      as rows_loaded,
    coalesce(sum(rows_rejected), 0)                                 as rows_rejected,
    coalesce(sum(raw_bytes), 0)                                     as raw_bytes,
    round(avg(duration_ms))                                         as avg_duration_ms,
    quantile_cont(duration_ms, 0.95)                                as p95_duration_ms,
    max(duration_ms)                                                as max_duration_ms
from {{ ref('fct_loads') }}
group by 1, 2
