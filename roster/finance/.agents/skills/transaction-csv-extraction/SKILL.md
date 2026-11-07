---
name: transaction-csv-extraction
description: Adaptive CSV import skill — previews a stored bank or card CSV with import_transactions(storage_ref, dry_run=true), fixes the column map if needed, then imports it in one call. Also covers a historical backfill of several statements, one import per stored file. Enforces strict token budget discipline throughout.
version: 2.1.0
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
   `"unrecognized_format"` means the columns or date format could not be
   detected; the response carries `headers` and `missing_columns` (go to
   Phase 3). `"error"` carries a message (blob not found, ...).
2. **`detected_format`** — `chase`, `amex`, `capital_one`, or `generic`.
3. **`date_format_detected`** — confirm it matches the `preview` dates (e.g. a UK
   statement must not parse `03/04` as March 4 when it means 3 April).
4. **`preview`** — up to 10 parsed transactions. If `preview` is empty or
   `parsed` is 0, nothing would import: do NOT run Phase 4. Fix the
   `column_map` (Phase 3) or report to the user. For each row, check `merchant`,
   `amount`, and `direction` (`debit` = money out, `credit` = money in). Charges
   on a card statement and purchases on a bank statement must be `debit`.
5. **`is_duplicate`** — preview rows already in the ledger.
6. **`parse_errors` / `error_details`** — rows that failed to parse, with a
   reason per row.
7. **`currency_warning`** — currency fell back to USD. If the statement is not
   USD, re-run the preview with `currency=<code>`.

---

## Phase 3: Adjust the column map (only when needed)

Supply a `column_map` (a JSON string) when the dry run returns
`status="unrecognized_format"`, or when the preview read the wrong column
(e.g. the merchant shows a reference number). Re-run the dry run with it:

```
import_transactions(
    storage_ref=<ref>,
    column_map='{"date": "Booking Date"}',
    dry_run=true,
)
```

- Every key is an override merged over auto-detection; no key is required.
  Name only the columns detection got wrong or missed.
- Keys: `date`, `merchant`, `amount`, plus optional `category` and
  `description`. `description` is NOT a substitute for `merchant`: the
  merchant is read only from the `merchant` key (or its auto-detected column).
- On `unrecognized_format`, build the map from the response: for each entry in
  `missing_columns`, pick the matching name from `headers` (e.g. `Booking Date`
  for `date`, `Payee Details` for `merchant`, `Value` for `amount`). If
  `missing_columns` is empty, the date column was found but its format was not
  recognized; check whether another header is the real date column.
- Ask the user only when `headers` leaves the choice ambiguous (two plausible
  columns for one key, or none that fits). Never read the file to find out.
- If `headers_truncated` is true, the echoed headers were capped (at most 50
  names of 100 characters each), so the list may be incomplete. Do not guess:
  ask the user which columns hold the date, amount, and merchant.

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
import_transactions(
    storage_ref=<ref>,
    account_id=<uuid or omit>,
    currency=<same or omit>,
    column_map=<same or omit>,
)
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

## Several Statements (Historical Backfill)

Use this when the user wants to load history: several months or several
accounts, or an intelligence tool returned `status="insufficient_data"` and no
history has been imported yet. It is the single-file flow repeated, one
`import_transactions` call per stored statement. There is no folder or batch
import: each statement needs its own `storage_ref`.

1. **Collect one `storage_ref` per statement.** Ask the user to upload or
   attach every export (Phase 1). Do not offer to scan a folder. Note which
   account each statement belongs to and resolve each `account_id`.
2. **Dry-run every file before importing any.** Run Phase 2 (and Phase 3 if
   needed) for each `storage_ref`, so a bad file surfaces before anything is
   written. Statements exported from the same institution usually share a
   format; a `column_map` that fixed one is the first thing to try on the next.
   The 3-preview limit applies per file.
3. **Import the files whose preview was right**, one Phase 4 call each, with
   that file's own `account_id`, `currency`, and `column_map`. Hold back any file
   whose preview failed and say so; do not let one bad file block the rest.
4. **Report per file, then the totals.** One line per statement (imported,
   skipped, errors), then the sums across all files.

Deduplication is the tool's job. Overlapping statements (two exports of one
account covering the same weeks) import their shared rows once; the repeats show
as `skipped`. A high skip
rate on the second of two overlapping exports is expected; warn as in Phase 5
only when it is not explained by an overlap the user described.

Baselines are recomputed by each import (`baselines_triggered`); there is no
separate baseline step to run afterwards. Suggest `budget-review` or
`spending-review` once history is loaded.

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

1. The dry run returns `status="unrecognized_format"` with
   `headers=["Booking Date", "Payee", "Value", "Balance"]` and
   `missing_columns=["date"]`. `Payee` and `Value` were detected on their own.
2. `Booking Date` is the only date-like header, so there is no need to ask the
   user. Re-run the dry run with `column_map='{"date": "Booking Date"}'` and
   `currency="EUR"`. The preview now shows dated rows with a supermarket
   purchase as a `debit` and a salary as a `credit`.
3. Import with the same `column_map` and `currency`, then report.

### Example 3: A year of history across two accounts

The user says spending analysis keeps reporting insufficient data and attaches
twelve monthly checking statements and one card export covering the same year.

1. Thirteen dry runs: the checking statements all detect as `chase`; the card
   export detects as `generic` with every charge previewing as `credit`.
2. Import the twelve checking statements against the checking `account_id`.
   Report each month's counts and the yearly total.
3. Hold back the card export and tell the user its amounts would import with
   the wrong direction (Phase 3, sign convention).

---

## Error Reference

| Response | Likely Cause | Fix |
|---|---|---|
| `status="refused"`, `attachment_forbidden` | `storage_ref` belongs to another butler | Ask the user to upload the CSV to finance |
| `status="refused"`, `invalid_storage_ref` | Malformed ref | Use the exact ref from the attachment or upload |
| `Blob storage is not configured` | Deployment has no blob store | Report to the user; import is unavailable |
| `Failed to load file from blob storage` | Ref points at a missing blob | Ask the user to re-upload |
| `status="unrecognized_format"` | Required column not detected, or date format unknown | Build `column_map` from `headers` and `missing_columns`; ask the user only if ambiguous |
| Empty `preview`, `parsed` is 0 | `column_map` points at the wrong columns | Fix the map; do not import |
| Every charge previews as `credit` | Positive-charge export detected as `generic` | Do not import; report the sign problem to the user |
| High skip rate (>20%) | Overlapping import or cross-source duplicates | Expected on re-run; verify with user if first run |
