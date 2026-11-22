-- A SKU identifies an inventory row. Quantity is a nonnegative integer.
CREATE TABLE IF NOT EXISTS inventory (
    sku TEXT PRIMARY KEY NOT NULL,
    qty INTEGER NOT NULL CHECK (typeof(qty) = 'integer' AND qty >= 0)
);
-- An idempotency key identifies one original request, not a SKU.
CREATE TABLE IF NOT EXISTS orders (
    request_key TEXT PRIMARY KEY NOT NULL,
    sku TEXT NOT NULL REFERENCES inventory(sku),
    qty INTEGER NOT NULL CHECK (typeof(qty) = 'integer' AND qty > 0),
    stock_left INTEGER NOT NULL
);
INSERT OR IGNORE INTO inventory(sku, qty) VALUES ('A', 5);
