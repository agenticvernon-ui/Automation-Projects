# AI Automation Portfolio

Small, tested automation prototypes for e-commerce, CRM, accounting and restaurant operations.
Each project lives in its own folder, runs with plain Python (no installs), and documents its requirements,
workflow logic, exception handling and troubleshooting.

> **Scope note:** these are prototypes built on simulated data (SQLite stands in for PostgreSQL; sample JSON
> stands in for live feeds). Integrations such as n8n, CRM, QuickBooks/Xero sandboxes are marked per project
> as built or not yet built. Nothing here has been used in production.

## Projects

| # | Project | Stack | Status |
|---|---------|-------|--------|
| 01 | [E-commerce Order-to-Fulfillment](projects/01-ecom-order-fulfillment) | Python, SQL (SQLite to PostgreSQL), n8n notes | Prototype, 7 tests passing |
| 02 | [E-commerce CRM & Customer Retention](projects/02-crm-retention) | Python, SQL, email-automation design, n8n notes | Prototype, 16 tests passing |
| 03 | [Accounting & Invoice Automation](projects/03-invoice-automation) | Python, SQL, accounting-system stand-in (QuickBooks/Xero design), n8n notes | Prototype, 37 tests passing |
| 04 | [POS-to-CRM Customer Sync](projects/04-pos-crm-sync) | Python, SQL, webhook-style inbox, CRM stand-in | Prototype, 12 tests passing |
| 05 | [Restaurant Inventory & Reorder](projects/05-restaurant-inventory) | Python, SQL, CSV for Google Sheets, n8n notes | Prototype, 18 tests passing |

## Run a project

```bash
cd projects/01-ecom-order-fulfillment
python3 demo.py                 # sample run
python3 -m unittest -v          # project tests
```

Run every project's tests from the repo root: `bash scripts/run_tests.sh` (also runs on every push via GitHub Actions).

## Repo layout

```
projects/NN-name/   one self-contained project (code, tests, sample data, README)
docs/               project template, GitHub web upload guide, git command guide
scripts/            test runner
.github/workflows/  CI
```

## Adding a project
Copy the structure in [docs/PROJECT_TEMPLATE.md](docs/PROJECT_TEMPLATE.md), add tests, and update the table above in the same commit.
