{{ config(materialized='external', location=lake_path('gold/agg_dataset_health.parquet')) }}

{#- Per dataset: how often it loads, how often it is quarantined, and the
    state of its last successful load. -#}
with latest as (
    select project_id, dataset, max(load_id) as last_success_load_id
    from {{ ref('fct_loads') }}
    where status = 'succeeded'
    group by 1, 2
)
select
    l.project_id,
    l.dataset,
    count(*)                                                as loads,
    count(*) filter (where l.status = 'succeeded')          as succeeded,
    count(*) filter (where l.status = 'quarantined')        as quarantined,
    count(*) filter (where l.status = 'failed')             as failed,
    min(l.created_at)                                       as first_load_at,
    max(l.created_at)                                       as last_load_at,
    latest.last_success_load_id,
    max(case when l.load_id = latest.last_success_load_id then l.rows_out end) as last_rows_out,
    max(case when l.load_id = latest.last_success_load_id then l.quality_status end) as last_quality_status,
    coalesce(sum(l.rows_rejected), 0)                       as rows_rejected_total,
    round(avg(l.duration_ms))                               as avg_duration_ms
from {{ ref('fct_loads') }} l
left join latest using (project_id, dataset)
group by 1, 2, 9
