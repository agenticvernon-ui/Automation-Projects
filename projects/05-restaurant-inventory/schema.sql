-- Restaurant inventory database. Portable to PostgreSQL (replace AUTOINCREMENT with IDENTITY).
-- Dates are ISO text (YYYY-MM-DD). Counts are taken at CLOSE of business on their date.
CREATE TABLE IF NOT EXISTS ingredients (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  unit           TEXT NOT NULL,
  supplier       TEXT NOT NULL,
  pack_size      REAL NOT NULL,          -- units per orderable pack
  lead_time_days INTEGER NOT NULL,
  unit_cost      REAL NOT NULL,          -- PHP per unit (simulated)
  min_threshold  REAL NOT NULL,          -- never let stock fall below this without a reorder
  safety_days    REAL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS recipes (
  dish          TEXT NOT NULL,
  ingredient_id TEXT NOT NULL,
  qty           REAL NOT NULL,           -- per portion
  PRIMARY KEY (dish, ingredient_id)
);
CREATE TABLE IF NOT EXISTS sales (
  sale_id TEXT PRIMARY KEY,              -- makes re-importing the same sales harmless
  day     TEXT NOT NULL,
  dish    TEXT NOT NULL,
  qty     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ingredient_id TEXT NOT NULL,
  day           TEXT NOT NULL,
  kind          TEXT NOT NULL,           -- COUNT (absolute), DELIVERY (+), USAGE (-), WASTE (-)
  qty           REAL NOT NULL,
  ref           TEXT NOT NULL,
  UNIQUE (ingredient_id, kind, ref)
);
CREATE TABLE IF NOT EXISTS variances (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ingredient_id TEXT NOT NULL,
  day           TEXT NOT NULL,
  expected      REAL NOT NULL,
  counted       REAL NOT NULL,
  variance      REAL NOT NULL,           -- counted minus expected
  tolerance     REAL NOT NULL,
  flag          TEXT NOT NULL            -- OPENING, OK, SHORTAGE, SURPLUS
);
CREATE TABLE IF NOT EXISTS purchase_requests (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier      TEXT NOT NULL,
  status        TEXT NOT NULL,           -- DRAFT, APPROVED, ORDERED, RECEIVED, REJECTED
  created_on    TEXT NOT NULL,
  approved_by   TEXT,
  approver_role TEXT,
  decided_on    TEXT,
  ordered_on    TEXT,
  est_total     REAL NOT NULL DEFAULT 0,
  note          TEXT
);
CREATE TABLE IF NOT EXISTS pr_lines (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  pr_id         INTEGER NOT NULL,
  ingredient_id TEXT NOT NULL,
  packs         INTEGER NOT NULL,
  qty           REAL NOT NULL,
  unit_cost     REAL NOT NULL,
  flags         TEXT,
  received_qty  REAL
);
