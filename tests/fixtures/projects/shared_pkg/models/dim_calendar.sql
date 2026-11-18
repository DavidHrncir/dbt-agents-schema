select cast('2024-01-01' as date) + i as calendar_date
from range(0, 366) as t(i)
