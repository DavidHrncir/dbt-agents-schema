select distinct channel
from {{ ref('channel_map') }}
