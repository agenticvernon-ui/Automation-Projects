-- Invoice automation database. Money is stored as integer centavos (never floats).
-- Portable to PostgreSQL (replace AUTOINCREMENT with IDENTITY).
CREATE TABLE IF NOT EXISTS customers (
  id                 TEXT PRIMARY KEY,
  name               TEXT,
  email              TEXT,
  billing_address    TEXT,
  terms_days         INTEGER NOT NULL DEFAULT 30,
  credit_limit_cents INTEGER,             -- NULL means no limit
  vat_exempt         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY,
  payload  TEXT NOT NULL,                 -- the approved order as received
  status   TEXT NOT NULL,                 -- NEW, DRAFTED, EXCEPTION
  overrides TEXT NOT NULL DEFAULT '[]'    -- exception codes a human has released
);
CREATE TABLE IF NOT EXISTS invoices (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id      TEXT NOT NULL UNIQUE,     -- one invoice per order, enforced by the database
  customer_id   TEXT NOT NULL,
  number        TEXT UNIQUE,              -- assigned at approval; numbers are never reused
  status        TEXT NOT NULL,            -- DRAFT, APPROVED, POSTED, PARTIALLY_PAID, PAID, VOID, REJECTED
  fingerprint   TEXT NOT NULL,
  created_on    TEXT NOT NULL,
  issue_date    TEXT,
  due_date      TEXT,
  terms_days    INTEGER NOT NULL,
  lines         TEXT NOT NULL,
  subtotal_c    INTEGER NOT NULL,
  discount_c    INTEGER NOT NULL,
  shipping_c    INTEGER NOT NULL,
  vat_c         INTEGER NOT NULL,
  total_c       INTEGER NOT NULL,
  approved_by   TEXT,
  approver_role TEXT,
  posted_at     TEXT,
  post_attempts INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  disputed      INTEGER NOT NULL DEFAULT 0   -- customer disputes it: no automated follow-ups
);
CREATE TABLE IF NOT EXISTS payments (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL,
  ref        TEXT NOT NULL UNIQUE,        -- bank/transaction reference: makes re-entry harmless
  amount_c   INTEGER NOT NULL,
  paid_on    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exceptions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id    TEXT NOT NULL,
  code        TEXT NOT NULL,
  severity    TEXT NOT NULL,              -- BLOCKING (fix the data) or REVIEW (a person may release)
  detail      TEXT NOT NULL,
  created_on  TEXT NOT NULL,
  resolved    INTEGER NOT NULL DEFAULT 0,
  resolved_by TEXT,
  resolution  TEXT
);
CREATE TABLE IF NOT EXISTS counters (
  name  TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  at     TEXT NOT NULL,
  actor  TEXT NOT NULL,
  action TEXT NOT NULL,
  ref    TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS followups (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id  INTEGER NOT NULL,
  stage       TEXT NOT NULL,              -- REMINDER_1, REMINDER_2, FINAL_REMINDER
  dedupe_key  TEXT NOT NULL UNIQUE,       -- invoice + stage + balance: one draft each, re-drafted if the balance changes
  subject     TEXT NOT NULL,
  body        TEXT NOT NULL,
  status      TEXT NOT NULL,              -- DRAFT, APPROVED, SENT, BLOCKED
  block_reason TEXT,
  created_on  TEXT NOT NULL,
  approved_by TEXT,
  sent_on     TEXT
);
