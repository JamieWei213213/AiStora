{#- One row per load manifest. Manifests are the pipeline's ledger, so this
    is the authoritative source for load outcomes; events are the timeline. -#}
{% set pattern = lake_path('manifests/*/*/*.json') %}
{% set columns = {
    'load_id': 'VARCHAR',
    'project_id': 'BIGINT',
    'dataset': 'VARCHAR',
    'mode': 'VARCHAR',
    'source': 'VARCHAR',
    'status': 'VARCHAR',
    'stage': 'VARCHAR',
    'created_at': 'TIMESTAMP',
    'finished_at': 'TIMESTAMP',
    'raw_bytes': 'BIGINT',
    'duration_ms': 'BIGINT',
    'counts': 'JSON',
    'quality': 'JSON',
    'error': 'JSON',
    'snapshot_id': 'BIGINT'
} %}

{% if lake_has_files(pattern) %}
select
    load_id,
    project_id,
    dataset,
    mode,
    source,
    status,
    stage                                                    as last_stage,
    created_at,
    finished_at,
    cast(created_at as date)                                 as dt,
    raw_bytes,
    duration_ms,
    try_cast(json_extract(counts, '$.rows_in') as bigint)        as rows_in,
    try_cast(json_extract(counts, '$.rows_out') as bigint)       as rows_out,
    try_cast(json_extract(counts, '$.rows_rejected') as bigint)  as rows_rejected,
    try_cast(json_extract(counts, '$.rows_inserted') as bigint)  as rows_inserted,
    try_cast(json_extract(counts, '$.rows_updated') as bigint)   as rows_updated,
    try_cast(json_extract(counts, '$.rows_skipped') as bigint)   as rows_skipped,
    try_cast(json_extract(counts, '$.columns') as bigint)        as column_count,
    json_extract_string(quality, '$.status')                     as quality_status,
    try_cast(json_extract(quality, '$.duplicate_rows') as bigint) as duplicate_rows,
    json_extract_string(error, '$.type')                         as error_type,
    snapshot_id
from {{ read_lake_json(pattern, columns) }}
where load_id is not null
{% else %}
select
    cast(null as varchar) as load_id, cast(null as bigint) as project_id, cast(null as varchar) as dataset,
    cast(null as varchar) as mode, cast(null as varchar) as source, cast(null as varchar) as status,
    cast(null as varchar) as last_stage, cast(null as timestamp) as created_at, cast(null as timestamp) as finished_at,
    cast(null as date) as dt, cast(null as bigint) as raw_bytes, cast(null as bigint) as duration_ms,
    cast(null as bigint) as rows_in, cast(null as bigint) as rows_out, cast(null as bigint) as rows_rejected,
    cast(null as bigint) as rows_inserted, cast(null as bigint) as rows_updated, cast(null as bigint) as rows_skipped,
    cast(null as bigint) as column_count, cast(null as varchar) as quality_status, cast(null as bigint) as duplicate_rows,
    cast(null as varchar) as error_type, cast(null as bigint) as snapshot_id
where false
{% endif %}
