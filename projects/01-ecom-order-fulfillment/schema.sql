-- Portable SQL: runs on SQLite (demo) and PostgreSQL (swap AUTOINCREMENT for SERIAL/IDENTITY).
CREATE TABLE IF NOT EXISTS customers (
  email        TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  ship_address TEXT NOT NULL,
  order_count  INTEGER NOT NULL DEFAULT 0,
  updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory (
  sku      TEXT PRIMARY KEY,
  on_hand  INTEGER NOT NULL,
  reserved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders (
  order_id    TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  status      TEXT NOT NULL,
  payload     TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status_history (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id   TEXT NOT NULL,
  old_status TEXT,
  new_status TEXT NOT NULL,
  note       TEXT,
  at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exceptions (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT NOT NULL,
  code     TEXT NOT NULL,
  detail   TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  at       TEXT NOT NULL
);
