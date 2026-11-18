{{ config(enabled=false) }}
select * from {{ ref('fct_revenue') }}
