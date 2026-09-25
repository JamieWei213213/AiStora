{#- Helpers for reading the lake's raw JSON artefacts.

    DuckDB refuses a glob that matches no files, and a fresh deployment has
    no events yet, so every staging model asks first and falls back to an
    empty relation with the same typed columns. -#}

{% macro lake_path(relative) -%}
  {{ var('lake_uri') ~ '/' ~ relative }}
{%- endmacro %}

{% macro lake_has_files(pattern) -%}
  {%- if execute -%}
    {%- set result = run_query("select count(*) from glob('" ~ pattern ~ "')") -%}
    {{ return(result.columns[0].values()[0] > 0) }}
  {%- else -%}
    {{ return(false) }}
  {%- endif -%}
{%- endmacro %}

{% macro read_lake_json(pattern, columns) -%}
  {#- Explicit columns: a key that no file carries yet still comes back as a
      typed NULL column, and the hive-style dt=/hour= path segments are not
      turned into surprise columns. -#}
  read_json('{{ pattern }}',
            columns = {
              {%- for name, type in columns.items() %}
              '{{ name }}': '{{ type }}'{{ "," if not loop.last }}
              {%- endfor %}
            },
            format = 'auto', hive_partitioning = false, ignore_errors = true,
            maximum_object_size = 33554432)
{%- endmacro %}
