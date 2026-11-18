select invoice_id, customer_id, invoice_date, amount, currency_code
from {{ source('erp', 'invoices') }}
