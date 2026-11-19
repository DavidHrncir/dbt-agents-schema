select campaign_id, spend_date, spend_usd, clicks
from {{ source('ads', 'spend') }}
