select
    c.campaign_id,
    m.channel,
    s.spend_date,
    s.spend_usd,
    s.clicks
from {{ ref('stg_ad_spend') }} as s
join {{ ref('stg_campaigns') }} as c using (campaign_id)
join {{ ref('channel_map') }} as m using (source_channel)
