{#- Two small generic tests so the project has no package dependency (a
    Lambda that runs `dbt deps` against the network at 03:00 is a failure
    waiting to happen). -#}

{% test dbt_utils_free_unique_combination(model, combination) %}
select {{ combination | join(', ') }}, count(*) as n
from {{ model }}
group by {{ combination | join(', ') }}
having count(*) > 1
{% endtest %}

{% test dbt_utils_free_between(model, column_name, low, high) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ low }} or {{ column_name }} > {{ high }})
{% endtest %}
