# Project 4: POS-to-CRM Customer Synchronization

**Problem:** a shop's point-of-sale (POS) records sales, but the CRM only stays useful if each sale lands on
the *right* customer profile without creating duplicates or silently dropping data.

**Run it:** `python3 demo.py` (sample run) and `python3 -m unittest -v` (12 tests). No installs needed.

## Honest scope
- The POS feed is simulated JSON, and the CRM is an in-memory stand-in (`FakeCRM`). No real CRM sandbox is connected yet.
- Matching handles Philippine mobile numbers only (`09xx`, `+639xx`, `9xx` formats). Landlines and other countries are
  treated as invalid and sent to review.
- Refunds (negative totals) are out of scope and go to review.

## Requirements
- Every POS transaction must end in exactly one of: synced, or waiting for a human. Nothing is lost or silently dropped.
- Never merge two customer profiles automatically.
- Never overwrite existing CRM data (only blanks are filled).
- A retry after an outage must not create duplicate profiles or double-count a purchase.

## Architecture

```
POS webhook -> receive() -> inbox (durable) -> process_pending() -> match/create in CRM -> record purchase
                                                      |                                      |
                                                      +--> FAILED (CRM down, auto-retried)  +--> txn_map (audit)
                                                      +--> QUARANTINED (human decides)
```

## Workflow logic
1. **Receive:** store the raw payload in the inbox first. A repeated `txn_id` is ignored (webhooks are often delivered twice).
2. **Validate:** well-formed email and phone, at least one line item, positive total, and total equals the sum of the lines.
3. **Match:** normalize email (trim, lower-case) and phone (to `+639XXXXXXXXX`), then look up the CRM by each.
   - one profile found → use it, fill any blank fields;
   - none found, with contact details and a name → create a profile;
   - no contact details → record as an anonymous sale (no profile);
   - several profiles, or email and phone pointing at different profiles → quarantine.
4. **Record the purchase** in the CRM, keyed by `txn_id`, then mark the inbox row DONE with an audit row in `txn_map`.
5. **Recover:** `process_pending()` retries FAILED rows (up to 5 attempts, then logs `GAVE_UP` for manual replay).
6. **Resolve:** after a human fixes the data or merges profiles, `resolve_quarantine()` replays the transaction.

## Exception handling
| Code | Cause | Who resolves it |
|---|---|---|
| `INCONSISTENT_DATA` | invalid email/phone, missing lines, total mismatch | Store staff correct the sale, then replay |
| `MATCH_CONFLICT` | email and phone belong to different profiles, or duplicates exist | Marketing/CRM owner merges profiles, then replay |
| `INSUFFICIENT_DATA` | contact details but no name | Staff add the name, then replay |
| `CRM_UNAVAILABLE` | CRM timeout or error | Automatic retry; manual replay after 5 failures |
| `DUPLICATE_WEBHOOK` | same `txn_id` delivered again | None (ignored, logged) |

## Recovery process
1. Check `summary(con)` for FAILED and QUARANTINED counts.
2. After a CRM outage, run `process_pending()`. Because purchases are keyed by `txn_id` and matching runs before
   creating, a transaction that failed halfway does not create a second profile (see the outage test).
3. For quarantined rows, fix the cause, then call `resolve_quarantine(con, crm, txn_id, corrected_payload)`.
4. Everything that happened is in `sync_log`.

## Integration notes
- **Webhook:** an n8n Webhook node (or a small web endpoint) calls `receive()`, and a Schedule node calls `process_pending()` every few minutes.
- **Real CRM:** implement the five methods of `FakeCRM` against your CRM's sandbox API and pass that client in. Check its rate limits and duplicate-detection features.
- **Privacy:** customer emails and phone numbers are personal data. Keep real data out of the repo, restrict who can read `sync_log`, and follow the Data Privacy Act obligations that apply to the real business.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| Rows stay FAILED | CRM down or credentials expired | Restore the CRM, then run `process_pending()` |
| Many `MATCH_CONFLICT` | Duplicate profiles already in the CRM | Merge in the CRM, then replay |
| Valid-looking number rejected | Landlines or non-PH numbers | Extend `norm_phone` for the formats you need |
| Customer count grew unexpectedly | Matching data differs (different spelling or number) | Review `txn_map` outcomes for `CREATED` |

## Next steps (not built yet)
Real CRM sandbox client, webhook endpoint with signature check, refund handling, fuzzy name matching with human confirmation.
