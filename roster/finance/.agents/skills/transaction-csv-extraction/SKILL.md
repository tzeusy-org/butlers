---
name: transaction-csv-extraction
description: Adaptive CSV import skill — samples the file, generates a stdlib-only Python script, and bulk-POSTs normalized transactions to /api/finance/transactions/bulk. Enforces strict token budget discipline throughout.
version: 1.0.0
---

# Transaction CSV Extraction Skill

This skill guides the finance butler through importing transaction history from a
CSV file. The workflow is: **sample → generate script → execute → report**.

---

## CRITICAL CONSTRAINTS

**READ THIS SECTION FIRST. These are hard rules, not suggestions.**

### MUST NOT: Read the entire CSV file into context

You MUST NOT read, display, or stream more than 15 lines of the CSV file into
your context window at any point during this workflow.

**Reason:** A typical bank statement with 500 rows consumes roughly 50,000
tokens. A full year of transactions can exceed 500,000 tokens. Reading a full
CSV into context is not an acceptable approach — it will exhaust the token
budget and may abort the session before any data is imported.

**The only CSV lines you may read into context are: the header row plus the
first 10 rows of data (≤ 15 lines total, including any BOM or blank rows).**

### MUST NOT: Call single-record tools in a loop

You MUST NOT call `record_transaction`, `record_transaction_fact`, or any other
single-record ingestion tool in a loop over CSV rows.

**Reason:** A 500-row CSV would require 500 separate MCP tool calls. Each
round-trip consumes tokens and time. For a modest statement, this generates
thousands of tokens of tool-call overhead and would take minutes to complete.
This approach is not acceptable regardless of file size.

**The ONLY acceptable ingestion path is a generated Python script that batches
100 rows at a time and POSTs them to the bulk HTTP endpoint.**

---

## Inputs

| Input | Required | Description |
|---|---|---|
| `file_path` | Yes | Absolute or relative path to the CSV file |
| `account_id` | No | UUID of the account these transactions belong to |
| `api_url` | No | Override for the dashboard API base URL |

---

## Phase 1: Sample the File

Read **only the first 15 lines** of the CSV file (header + first 10 data rows,
plus allowance for a BOM marker or blank leading rows). Do not read more.

From this sample, infer:

1. **Delimiter** — comma, semicolon, tab, or pipe. Identify from the header row.
2. **Header row** — the exact column names as they appear, case-preserved.
3. **Column mapping** — identify which column carries each of:
   - Date / posting date
   - Merchant / description / payee
   - Amount (single column, or split Debit + Credit columns)
   - Transaction type indicator if present (e.g. "Type", "D/C", "CR/DR")
   - Any other available fields (reference, category, balance)
4. **Date format** — e.g. `%Y-%m-%d`, `%m/%d/%Y`, `%d/%m/%Y`, `%b %d, %Y`.
   Infer from the sample data values, not the column name.
5. **Amount sign convention** — determine which of these applies:
   - **Single signed column**: negative = debit, positive = credit (already canonical).
   - **Single unsigned column + type indicator**: debits are positive, negate them.
   - **Split debit/credit columns**: Debit column values → negate to negative; Credit column values → keep positive.
   - **All positive (credit card statement style)**: all charges are debits; negate all amounts. Refunds/payments identifiable from description or type column stay positive.
6. **BOM marker** — note if the file starts with `\ufeff` so the script handles it.
7. **Currency** — infer from column headers, file name, or sample values. Default to `USD` only when context is unambiguously US.

Summarize your inferences in a brief internal note before writing the script.
Do not ask the user to confirm the mapping unless the sample is genuinely
ambiguous (e.g., date format cannot be determined from 10 rows).

---

## Phase 2: Generate the Script

Write a self-contained Python script named `import_csv.py` (or a similarly
descriptive name) and save it to disk. The script MUST:

### Stdlib-only

Use only Python standard library modules. Permitted: `csv`, `json`,
`urllib.request`, `urllib.error`, `urllib.parse`, `datetime`, `hashlib`, `sys`,
`os`, `argparse`, `pathlib`. Do NOT import `requests`, `pandas`, `httpx`, or
any third-party package.

### CLI interface

```
python import_csv.py <csv_file> [--account-id UUID] [--api-url URL]
```

- `csv_file` — positional argument: path to the CSV file.
- `--account-id` — optional account UUID.
- `--api-url` — optional API base URL. Falls back to `BUTLERS_API_URL` environment
  variable, then to `http://localhost:8000`.

### Pre-flight connectivity check

Before reading any CSV rows, send a HEAD request to `<api_url>/api/finance/transactions/bulk`.
If the connection is refused or the server returns an unexpected error, print:

```
ERROR: Dashboard API not reachable at <api_url>
```

and exit with code 1. No transactions should be processed if the check fails.

### BOM handling

Open the file with `encoding="utf-8-sig"` to transparently strip the BOM marker
if present.

### Row normalization

For each data row, map columns to the canonical transaction schema:

| Field | Type | Notes |
|---|---|---|
| `posted_at` | ISO-8601 string with `Z` suffix | Parse with inferred date format; set time to `T00:00:00Z` when only a date is available |
| `merchant` | string | Raw value from the description/payee column; do not normalize |
| `amount` | string (decimal) | Apply sign normalization; format as `"-47.32"` not `-47.32` |
| `currency` | string | ISO-4217 uppercase; e.g. `"USD"` |
| `category` | string or null | Pass through if present in CSV; omit if absent |
| `description` | string or null | Any additional context column; omit if absent |
| `payment_method` | string or null | Omit if absent |

Skip rows where date or amount cannot be parsed; count them in `errors`.
Skip completely blank rows silently (do not count as errors).

### Amount sign normalization (canonical: negative = debit, positive = credit)

Apply exactly one of the following based on your Phase 1 inference:

- **Single signed column** — use value as-is.
- **Single unsigned column + type indicator** — negate if type indicates debit
  (`"debit"`, `"d"`, `"dr"`, `"withdrawal"`, `"purchase"`, case-insensitive);
  keep positive if type indicates credit.
- **Split debit/credit columns** — `amount = -(debit_value) if debit_value else +(credit_value)`.
- **All-positive (credit card style)** — negate all amounts; keep positive for
  rows where the type or description clearly indicates a payment, refund, or credit.

### Batch POST

Group normalized rows into batches of 100. For each batch, POST to
`<api_url>/api/finance/transactions/bulk` with JSON body:

```json
{
  "account_id": "<uuid or omitted>",
  "source": "csv-import",
  "transactions": [ ... ]
}
```

Request headers: `Content-Type: application/json`.

Parse the JSON response. Accumulate `imported`, `skipped`, and `errors` across
all batches. If a batch request fails with a non-2xx status or a network error,
print the error to stderr and count all rows in that batch as errors.

### JSON summary to stdout

When all batches are complete, print a single JSON object to stdout:

```json
{"total": 500, "imported": 487, "skipped": 11, "errors": 2}
```

No other output to stdout. Diagnostic messages go to stderr.

### Exit codes

- `0` — completed (even if some rows were skipped or errored).
- `1` — fatal error (API unreachable, file not found, unrecoverable parse failure).

---

## Phase 3: Execute and Self-Correct

Run the generated script. Read stderr if the exit code is non-zero.

If the script fails:

1. Read the error output (stderr).
2. Diagnose the issue from the traceback or error message.
3. Fix the script — the most common problems are:
   - Wrong date format string for the inferred format.
   - Column name mismatch (check exact header from sample).
   - Debit/credit column logic for a split-column layout.
   - Missing `encoding="utf-8-sig"` on the `open()` call.
4. Save the corrected script and re-run.

**Maximum 3 self-correction attempts.** If the script still fails after 3 tries,
report the failure to the user with the last error output and your diagnosis.
Do not attempt a 4th correction without user input.

---

## Phase 4: Parse and Report Results

After the script exits with code 0, parse the JSON summary from stdout.

Report to the user:

```
CSV import complete.

Total rows:    500
Imported:      487
Skipped:        11  (duplicates — already in your transaction history)
Errors:          2  (rows that could not be parsed; see stderr output above)
```

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

### Example 1: Simple Standard CSV

**File**: `chase_checking_jan2025.csv`

**Sample (first 3 data rows)**:
```
Date,Description,Amount
2025-01-03,WHOLEFDS MKT #10456,-47.32
2025-01-04,NETFLIX.COM,-15.99
2025-01-05,DIRECT DEPOSIT EMPLOYER,2500.00
```

**Phase 1 inferences**:
- Delimiter: comma
- Date column: `Date`, format `%Y-%m-%d`
- Merchant column: `Description`
- Amount column: `Amount`, already signed (negative = debit, positive = credit)
- No type indicator column
- Currency: USD (inferred from context)

**Generated script (key logic)**:
```python
import csv, json, urllib.request, sys, argparse, os

parser = argparse.ArgumentParser()
parser.add_argument("csv_file")
parser.add_argument("--account-id", default=None)
parser.add_argument("--api-url", default=None)
args = parser.parse_args()

api_url = args.api_url or os.environ.get("BUTLERS_API_URL", "http://localhost:8000")

# Pre-flight
try:
    req = urllib.request.Request(f"{api_url}/api/finance/transactions/bulk", method="HEAD")
    urllib.request.urlopen(req, timeout=5)
except Exception as e:
    print(f"ERROR: Dashboard API not reachable at {api_url}", file=sys.stderr)
    sys.exit(1)

totals = {"total": 0, "imported": 0, "skipped": 0, "errors": 0}
batch = []

def post_batch(batch):
    body = json.dumps({
        "account_id": args.account_id,
        "source": "csv-import",
        "transactions": batch,
    }).encode()
    req = urllib.request.Request(
        f"{api_url}/api/finance/transactions/bulk",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

with open(args.csv_file, encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        if not any(row.values()):
            continue
        totals["total"] += 1
        try:
            from datetime import datetime, timezone
            dt = datetime.strptime(row["Date"].strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            amount = str(float(row["Amount"].strip().replace(",", "")))
            batch.append({
                "posted_at": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "merchant": row["Description"].strip(),
                "amount": amount,
                "currency": "USD",
            })
        except Exception as e:
            print(f"Row error: {e}", file=sys.stderr)
            totals["errors"] += 1
            continue

        if len(batch) >= 100:
            result = post_batch(batch)
            totals["imported"] += result["imported"]
            totals["skipped"] += result["skipped"]
            totals["errors"] += result.get("errors", 0)
            batch = []

if batch:
    result = post_batch(batch)
    totals["imported"] += result["imported"]
    totals["skipped"] += result["skipped"]
    totals["errors"] += result.get("errors", 0)

print(json.dumps(totals))
```

**Result report**:
```
CSV import complete.

Total rows:   47
Imported:     45
Skipped:       2  (duplicates)
Errors:        0
```

---

### Example 2: Split Debit/Credit Columns + Non-Standard Date Format

**File**: `barclays_account_q1_2025.csv`

**Sample (first 3 data rows)**:
```
Transaction Date,Narrative,Debit Amount,Credit Amount,Balance
15/01/2025,TESCO STORES 3456,47.50,,1243.20
18/01/2025,SALARY PAYMENT,,2200.00,3443.20
20/01/2025,AMAZON.CO.UK,12.99,,3430.21
```

**Phase 1 inferences**:
- Delimiter: comma
- Date column: `Transaction Date`, format `%d/%m/%Y`
- Merchant column: `Narrative`
- Amount: split columns — `Debit Amount` (positive values = money out) and `Credit Amount` (positive values = money in)
- Sign convention: negate Debit Amount values; keep Credit Amount values positive
- Currency: GBP (inferred from Barclays UK context)

**Key normalization logic in generated script**:
```python
debit_raw = row.get("Debit Amount", "").strip().replace(",", "")
credit_raw = row.get("Credit Amount", "").strip().replace(",", "")

if debit_raw:
    amount = str(-abs(float(debit_raw)))   # debit → negative
elif credit_raw:
    amount = str(abs(float(credit_raw)))   # credit → positive
else:
    raise ValueError("Neither debit nor credit value present")
```

**Date parsing**:
```python
dt = datetime.strptime(row["Transaction Date"].strip(), "%d/%m/%Y").replace(tzinfo=timezone.utc)
```

**Result report**:
```
CSV import complete.

Total rows:   91
Imported:     89
Skipped:       1  (duplicate)
Errors:        1  (one row had both Debit Amount and Credit Amount blank)
```

---

## Error Reference

| Error | Likely Cause | Fix |
|---|---|---|
| `Dashboard API not reachable` | Butler daemon not running | Start the butler daemon; retry |
| `KeyError: 'Date'` | Column name mismatch | Check exact header spelling in sample; update script |
| `ValueError: time data does not match format` | Wrong date format string | Re-examine sample dates; update `strptime` pattern |
| `ImportError: No module named 'requests'` | Script used non-stdlib import | Rewrite using `urllib.request` only |
| `json.decoder.JSONDecodeError` | API returned non-JSON (HTML error page) | Check API URL is correct; verify butler is healthy |
| High skip rate (>20%) | Overlapping import or cross-source duplicates | Expected on re-run; verify with user if first run |
