select campaign_id, campaign_name, source_channel
from {{ source('ads', 'campaigns') }}
