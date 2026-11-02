---
name: transaction-csv-extraction
description: Adaptive CSV import skill — previews a stored bank or card CSV with import_transactions(storage_ref, dry_run=true), fixes the column map if needed, then imports it in one call. Enforces strict token budget discipline throughout.
version: 2.0.0
---

# Transaction CSV Extraction Skill

This skill guides the finance butler through importing transaction history from a
bank or card CSV export. The workflow is: **locate → preview → adjust → import → report**.

All parsing, format detection, date and sign normalization, deduplication, and
batching happen inside the `import_transactions` tool. Your job is to pick the
right inputs, check the preview, and report the result.

---

## CRITICAL CONSTRAINTS

**READ THIS SECTION FIRST. These are hard rules, not suggestions.**

### MUST NOT: Place the CSV file into context

You MUST NOT read, display, or stream the CSV file into your context window.
The only CSV content you may see is what `import_transactions` returns: the
dry-run `preview` (at most 10 transactions) and `error_details`.

**Reason:** Reading a full CSV into context would consume hundreds of thousands
of tokens and is not an acceptable approach. A 500-row statement is roughly
50,000 tokens; a year of history can exceed 500,000.

### MUST NOT: Read a local file path

You MUST NOT open, `cat`, `head`, or otherwise read a CSV from a local
filesystem path, and you MUST NOT write or run a script that does. If the user
gives you a local path, ask them to upload the file instead (see Phase 1).

**Reason:** `storage_ref` is the only way to name CSV bytes for import. Finance
sessions read only blobs under this butler's storage prefix; reading arbitrary
paths would let a session reach any file the daemon can.

### MUST NOT: Call single-record tools in a loop

You MUST NOT call `record_transaction`, `record_transaction_fact`, or any other
single-record ingestion tool in a loop over CSV rows.

**Reason:** Calling a tool per CSV row would generate thousands of MCP
round-trips and consume an unacceptable number of tokens.

**The ONLY acceptable ingestion path is `import_transactions(storage_ref, ...)`.**

---

## Inputs

| Input | Required | Description |
|---|---|---|
| `storage_ref` | Yes | Blob reference to the uploaded CSV (e.g. `s3://bucket/finance/...` or an attachment's `storage_ref` under `connectors/`) |
| `account_id` | No | UUID of the account these transactions belong to |
| `currency` | No | ISO-4217 code when the CSV has no currency column and the account has none on file |

---

## Phase 1: Locate the CSV

You need a `storage_ref`.

- **CSV arrived as an attachment** (email, Telegram): use the attachment's
  `storage_ref` from the routed message's `payload.attachments`.
- **User uploaded through the dashboard**: use the `storage_ref` the upload
  returned.
- **User gave only a local path or pasted a filename**: do not read it. Ask the
  user to upload the CSV through the dashboard or send it as an attachment, then
  continue with the resulting `storage_ref`.

If the user named an account, resolve its `account_id` before importing so
dedup and currency inference use it.

---

## Phase 2: Preview (dry run)

Call:

```
import_transactions(storage_ref=<ref>, account_id=<uuid or omit>, dry_run=true)
```

Nothing is written. Check the response:

1. **`status`** — `"refused"` means the ref is outside this butler's prefix
   (`reason="attachment_forbidden"`) or malformed (`reason="invalid_storage_ref"`).
   Do not retry with a different guess; ask the user for the correct upload.
   `"error"` carries a message (blob not found, date format not detected, ...).
2. **`detected_format`** — `chase`, `amex`, `capital_one`, or `generic`.
3. **`date_format_detected`** — confirm it matches the `preview` dates (e.g. a UK
   statement must not parse `03/04` as March 4 when it means 3 April).
4. **`preview`** — up to 10 parsed transactions. For each, check `merchant`,
   `amount`, and `direction` (`debit` = money out, `credit` = money in). Charges
   on a card statement and purchases on a bank statement must be `debit`.
5. **`is_duplicate`** — preview rows already in the ledger.
6. **`parse_errors` / `error_details`** — rows that failed to parse. Each entry's
   `raw` keys are the CSV header names; use them when building a `column_map`.
7. **`currency_warning`** — currency fell back to USD. If the statement is not
   USD, re-run the preview with `currency=<code>`.

---

## Phase 3: Adjust the column map (only when needed)

If the preview picked the wrong columns, or the response is an error saying the
date column could not be found, re-run the dry run with an explicit
`column_map` (a JSON string):

```
import_transactions(
    storage_ref=<ref>,
    column_map='{"date": "Txn Date", "merchant": "Payee", "amount": "Value"}',
    dry_run=true,
)
```

- Required keys: `date`, `merchant` (or `description`), `amount`.
- Optional keys: `category`, `description`.
- Take column names from `error_details[].raw` keys when present; otherwise ask
  the user for the header row. Never read the file to find them.

**Sign convention you cannot fix.** `column_map` chooses columns; it does not
flip signs. If the preview shows every charge as `credit` (common for
credit-card exports that list charges as positive numbers and were detected as
`generic`), STOP. Tell the user the amounts would import with the wrong
direction and do not run the import.

**Maximum 3 preview attempts.** If the preview is still wrong after 3 tries,
report the last response and your diagnosis to the user. Do not try a 4th
without user input.

---

## Phase 4: Import

When the preview looks right, call `import_transactions` again with the same
`storage_ref`, `account_id`, `currency`, and `column_map`, and without
`dry_run`:

```
import_transactions(storage_ref=<ref>, account_id=<uuid or omit>, column_map=<same or omit>)
```

The tool inserts in batches, skips duplicates, sends progress notifications for
large files, and runs post-import steps (`baselines_triggered`,
`categories_learned`, `merchant_mappings_applied`).

---

## Phase 5: Report Results

Report to the user from the response:

```
CSV import complete.

Total rows:    500
Imported:      487
Skipped:        11  (duplicates — already in your transaction history)
Errors:          2  (rows that could not be parsed)
```

Mention `baselines_triggered=true` (spending baselines were recomputed) and a
non-zero `categories_learned` when present.

### High skip-rate warning

If `skipped / total > 0.20` (more than 20% of rows were skipped), warn the user:

> Warning: More than 20% of rows were skipped as duplicates. This may mean some
> transactions were already imported from another source, or that this CSV
> overlaps with a previous import. Verify that the imported count looks correct
> for this file.

### Re-run semantics

If the user mentions this is a re-run after a partial failure, explain:

> Rows that were successfully imported in the previous run will appear as
> "skipped (duplicate)" — this is expected and is not data loss. The meaningful
> result is the newly imported count from this run.

---

## Worked Examples

### Example 1: Known format, straight through

The user emails `chase_checking_jan2025.csv`; the routed message carries its
`storage_ref`.

1. `import_transactions(storage_ref=..., dry_run=true)` returns
   `detected_format="chase"`, `date_format_detected="%m/%d/%Y"`, and a preview
   where `WHOLEFDS MKT` is a `debit` and `DIRECT DEPOSIT` is a `credit`.
2. The preview is right, so `import_transactions(storage_ref=...)`.
3. Response `{total: 47, imported: 45, skipped: 2, errors: 0}` → report it.

### Example 2: Unrecognized date column needs a column map

A dashboard upload of a European bank export whose date column is named
`Booking Date`.

1. The dry run returns `status="error"` with `Could not auto-detect date
   format. Sample values: []` — no date column was recognized, and no rows were
   parsed, so there are no `error_details` to read headers from.
2. Ask the user for the header row. They reply
   `Booking Date,Payee,Value,Balance`.
3. Re-run the dry run with `column_map='{"date": "Booking Date"}'` and
   `currency="EUR"`. `Payee` and `Value` are recognized on their own, so the
   preview now shows dated rows with a supermarket purchase as a `debit` and a
   salary as a `credit`.
4. Import with the same `column_map` and `currency`, then report.

---

## Error Reference

| Response | Likely Cause | Fix |
|---|---|---|
| `status="refused"`, `attachment_forbidden` | `storage_ref` belongs to another butler | Ask the user to upload the CSV to finance |
| `status="refused"`, `invalid_storage_ref` | Malformed ref | Use the exact ref from the attachment or upload |
| `Blob storage is not configured` | Deployment has no blob store | Report to the user; import is unavailable |
| `Failed to load file from blob storage` | Ref points at a missing blob | Ask the user to re-upload |
| `Could not auto-detect date format` | Date column missing or unusual | Supply `column_map` with `date`; ask the user for headers if unknown |
| Every charge previews as `credit` | Positive-charge export detected as `generic` | Do not import; report the sign problem to the user |
| High skip rate (>20%) | Overlapping import or cross-source duplicates | Expected on re-run; verify with user if first run |
