select payment_id, invoice_id, paid_at, amount
from {{ source('erp', 'payments') }}
