-- Reconciler-local state. See phase-1-setup.md §4.4.
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY,
    grocy_shopping_location_id INTEGER UNIQUE,
    name TEXT NOT NULL,
    fulfillment_mode TEXT,
    min_order_total REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    week_start DATE NOT NULL,
    status TEXT NOT NULL,
    total_estimate REAL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    submitted_at DATETIME,
    received_at DATETIME
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    grocy_product_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    vendor_sku TEXT,
    vendor_url TEXT,
    last_known_price REAL,
    estimated_total REAL,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    id INTEGER PRIMARY KEY,
    week_start DATE NOT NULL,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME,
    meal_plan_recipe_count INTEGER,
    products_below_min INTEGER,
    products_added_for_meals INTEGER,
    unmapped_foods_count INTEGER,
    notes TEXT
);
