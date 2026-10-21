## Why

Finance has `payee`, `payee_key`, `payment_method` and `account_id` on a bill but no destination identity, so a changed payment destination on an otherwise familiar bill (invoice-redirect fraud) is invisible. This is slice S1 of the payment-redirect guard (bu-s11n0s.9): detection only, Finance-local, no sessions, no notification.

## What Changes

- `track_bill` gains an optional `payment_destination`, fingerprinted with a keyed HMAC; Finance keeps the raw value out of its own tables, the SPO mirror, its logs and the tool result. Known exception: session tool-call records (`sessions.tool_calls`) keep raw tool arguments today; the strip-to-allowlist fix is follow-up bu-4mfhdq.
- New table `finance.payment_destinations` and two nullable `finance.bills` columns (`destination_fingerprint`, `destination_verdict`), additive (Finance migration `finance_018`).
- The HMAC key `FINANCE_PAYMENT_DESTINATION_KEY` is resolved once at Finance module startup from the shared credential store (`env_fallback=False`) and registered in the secrets catalog as used by Finance.
- New capability `finance-payment-destination-integrity` (added directly under `openspec/specs/`) and this change's `finance-crud-operations` delta for the tool parameter.

## Non-Goals

The joint sender, destination and verified-channel check and the fleet case (S3), the owner "confirmed by phone" promotion verb (S4), sender authentication (bu-s11n0s.20), any notify or attention item, and any change to how sessions extract bills beyond the documented optional parameter. Provisioning the key in dev or prod is an owner or operations step.

## Impact

Affected: `roster/finance` (tools, module, migration, tests, tool-reference skill), `src/butlers/api/routers/secrets_v2.py` (catalog). Rollback: revert the PR; the migration downgrade drops the table and columns.
