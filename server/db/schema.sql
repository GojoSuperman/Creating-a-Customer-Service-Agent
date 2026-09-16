PRAGMA journal_mode = WAL;
CREATE TABLE categories (
  key TEXT PRIMARY KEY, label TEXT NOT NULL, free_shipping_threshold INTEGER, free_shipping_note TEXT,
  return_window_days INTEGER NOT NULL, return_window_basis TEXT NOT NULL, requires_unopened INTEGER NOT NULL DEFAULT 0);
CREATE TABLE same_day_delivery (region TEXT PRIMARY KEY, available INTEGER NOT NULL, fee INTEGER, extra_fee INTEGER, note TEXT);
CREATE TABLE customers (
  customer_id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL UNIQUE,
  address_region TEXT NOT NULL, address TEXT NOT NULL, joined_at TEXT NOT NULL);
CREATE TABLE products (
  product_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL REFERENCES categories(key),
  price INTEGER NOT NULL, stock INTEGER, is_set INTEGER NOT NULL DEFAULT 0, components TEXT, material TEXT, material_note TEXT,
  origin TEXT, has_quality_cert INTEGER NOT NULL DEFAULT 0, made_to_order INTEGER NOT NULL DEFAULT 0, made_to_order_days INTEGER,
  options TEXT, size_chart TEXT, size_matching TEXT, individual_purchase_allowed INTEGER, individual_purchase_note TEXT,
  individual_prices TEXT, return_allowed INTEGER, return_blocked_reason TEXT, soldout INTEGER NOT NULL DEFAULT 0,
  soldout_note TEXT, stock_note TEXT);
CREATE TABLE orders (
  order_id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(customer_id), ordered_at TEXT NOT NULL,
  status TEXT NOT NULL, status_detail TEXT, is_external_channel INTEGER NOT NULL DEFAULT 0, external_channel_name TEXT,
  order_amount INTEGER NOT NULL, shipping_fee INTEGER NOT NULL, free_shipping_applied INTEGER NOT NULL DEFAULT 0,
  address_region TEXT, courier TEXT, tracking_no TEXT, invoice_printed INTEGER NOT NULL DEFAULT 0,
  expected_ship_date TEXT, shipped_at TEXT, expected_delivery TEXT, delivered_at TEXT, delay_days INTEGER, delay_reason TEXT,
  return_id TEXT, note TEXT);
CREATE TABLE order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL REFERENCES orders(order_id),
  product_id TEXT NOT NULL REFERENCES products(product_id), name TEXT NOT NULL, option TEXT, qty INTEGER NOT NULL, price INTEGER NOT NULL);
CREATE TABLE shipment_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL REFERENCES orders(order_id), at TEXT NOT NULL, status TEXT NOT NULL, location TEXT);
CREATE TABLE returns (
  return_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id), type TEXT NOT NULL, return_scope TEXT,
  reason_stated TEXT, requested_at TEXT NOT NULL, stage TEXT NOT NULL, inspection_result TEXT, fault_party TEXT,
  shipping_fee_bearer TEXT, return_fee INTEGER, refund_amount INTEGER, refund_calc TEXT, expected_completion TEXT,
  exchange_target TEXT, exchange_available INTEGER, exchange_blocked_reason TEXT, convert_to_refund INTEGER,
  courier_visit_expected TEXT, note TEXT);
CREATE TABLE return_stage_history (id INTEGER PRIMARY KEY AUTOINCREMENT, return_id TEXT NOT NULL REFERENCES returns(return_id), stage TEXT NOT NULL, date TEXT NOT NULL);
CREATE TABLE restock (
  product_id TEXT PRIMARY KEY REFERENCES products(product_id), name TEXT NOT NULL, is_soldout INTEGER NOT NULL,
  is_confirmed INTEGER NOT NULL, expected_date TEXT, expected_note TEXT, notify_available INTEGER NOT NULL DEFAULT 1);
CREATE TABLE call_logs (call_id TEXT PRIMARY KEY, customer_id TEXT, started_at TEXT NOT NULL, ended_at TEXT, turns TEXT NOT NULL DEFAULT '[]');
CREATE INDEX idx_orders_customer ON orders(customer_id, ordered_at DESC);
CREATE INDEX idx_returns_order ON returns(order_id);
CREATE INDEX idx_items_order ON order_items(order_id);
