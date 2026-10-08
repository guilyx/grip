-- migrate:up transaction:false
CREATE INDEX CONCURRENTLY IF NOT EXISTS orders_open_by_customer
    ON orders (customer_id)
    WHERE status IN ('open', 'paid');

-- migrate:up
UPDATE orders o
SET customer_id = lo.customer_id
FROM customers_orders lo
WHERE o.id = lo.order_id AND o.customer_id IS NULL;

ALTER TABLE orders ALTER COLUMN customer_id SET NOT NULL;

-- migrate:down
ALTER TABLE orders ALTER COLUMN customer_id DROP NOT NULL;
DROP INDEX IF EXISTS orders_open_by_customer;
