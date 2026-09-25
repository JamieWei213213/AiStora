{{ config(materialized='external', location=lake_path('gold/agg_daily_agent.parquet')) }}

{#- Daily agent usage and spend per project. -#}
select
    dt,
    project_id,
    count(*)                                                        as runs,
    count(*) filter (where status = 'finished')                     as finished,
    round(count(*) filter (where status = 'finished') * 1.0 / count(*), 4) as success_rate,
    round(avg(case when verification_passed then 1 else 0 end), 4)  as verification_pass_rate,
    count(*) filter (where routing_tier = 'advanced')               as advanced_runs,
    sum(input_tokens)                                               as input_tokens,
    sum(output_tokens)                                              as output_tokens,
    round(sum(estimated_cost_usd), 6)                               as estimated_cost_usd,
    round(avg(duration_ms))                                         as avg_duration_ms,
    quantile_cont(duration_ms, 0.95)                                as p95_duration_ms,
    round(avg(tool_calls), 2)                                       as avg_tool_calls
from {{ ref('fct_agent_runs') }}
group by 1, 2
