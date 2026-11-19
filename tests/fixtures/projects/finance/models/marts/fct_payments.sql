select p.payment_id, p.invoice_id, p.paid_at, p.amount
from {{ ref('stg_payments') }} as p
