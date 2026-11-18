select
    l.invoice_date,
    l.customer_id,
    l.amount * fx.rate_to_usd as revenue_usd
from {{ ref('int_invoice_lines') }} as l
join {{ ref('fx_rates') }} as fx using (currency_code)
join {{ ref('shared_pkg', 'dim_calendar') }} as c on c.calendar_date = l.invoice_date
