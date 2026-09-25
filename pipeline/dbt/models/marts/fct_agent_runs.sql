{{ config(materialized='external', location=lake_path('gold/fct_agent_runs.parquet')) }}

{#- One row per completed agent run (question answered by the LLM agent).
    Cost is whatever the app estimated from configured per-token prices. -#}
select
    request_id,
    ts,
    dt,
    project_id,
    user_id,
    status,
    routing_tier,
    model,
    error_type,
    duration_ms,
    turns,
    tool_calls,
    coalesce(input_tokens, 0)          as input_tokens,
    coalesce(output_tokens, 0)         as output_tokens,
    coalesce(estimated_cost_usd, 0.0)  as estimated_cost_usd,
    verification_passed
from {{ ref('stg_events') }}
where event = 'agent.run'
  and request_id is not null
