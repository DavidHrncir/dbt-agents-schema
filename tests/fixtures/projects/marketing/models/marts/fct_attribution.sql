select p.channel, c.calendar_date, sum(p.spend_usd) as spend_usd
from {{ ref('fct_campaign_performance') }} as p
join {{ ref('shared_pkg', 'dim_calendar') }} as c on c.calendar_date = p.spend_date
group by 1, 2
