-- Local sync database. The CRM itself is external; this DB records what was received, what happened to it,
-- and what needs a human. Portable to PostgreSQL (replace AUTOINCREMENT with IDENTITY).
CREATE TABLE IF NOT EXISTS inbox (
  txn_id      TEXT PRIMARY KEY,
  payload     TEXT NOT NULL,
  status      TEXT NOT NULL,            -- PENDING, DONE, FAILED, QUARANTINED
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS txn_map (
  txn_id    TEXT PRIMARY KEY,
  crm_id    TEXT,                       -- NULL for anonymous walk-in sales
  outcome   TEXT NOT NULL,              -- MATCHED_EMAIL, MATCHED_PHONE, CREATED, ANONYMOUS
  synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quarantine (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  txn_id   TEXT NOT NULL,
  code     TEXT NOT NULL,
  detail   TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  txn_id TEXT,
  event  TEXT NOT NULL,
  detail TEXT,
  at     TEXT NOT NULL
);
