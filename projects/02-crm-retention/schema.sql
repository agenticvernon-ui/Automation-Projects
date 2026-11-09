-- Retention database. Portable to PostgreSQL (replace AUTOINCREMENT with IDENTITY).
CREATE TABLE IF NOT EXISTS customers (
  id               TEXT PRIMARY KEY,
  email            TEXT NOT NULL UNIQUE,
  name             TEXT NOT NULL,
  marketing_opt_in INTEGER NOT NULL DEFAULT 0,
  unsubscribed_at  TEXT,
  suppressed_reason TEXT,                -- e.g. hard bounce, staff flag
  open_complaint   INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  order_id    TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  total       REAL NOT NULL,
  ordered_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS carts (
  cart_id     TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  total       REAL NOT NULL,
  updated_at  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE TABLE IF NOT EXISTS messages (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id  TEXT NOT NULL,
  campaign     TEXT NOT NULL,           -- POSTPURCHASE, NUDGE, CART, WINBACK
  dedupe_key   TEXT NOT NULL UNIQUE,    -- one message per trigger, even if a job runs twice
  subject      TEXT NOT NULL,
  body         TEXT NOT NULL,
  status       TEXT NOT NULL,           -- DRAFT, APPROVED, SENT, REJECTED, BLOCKED, EXPIRED
  block_reason TEXT,
  approved_by  TEXT,
  approved_at  TEXT,
  created_at   TEXT NOT NULL,
  sent_at      TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL,
  type       TEXT NOT NULL,             -- OPENED, CLICKED, UNSUBSCRIBED, COMPLAINED
  at         TEXT NOT NULL
);
