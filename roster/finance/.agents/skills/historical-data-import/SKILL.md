---
name: historical-data-import
description: Multi-format CSV import workflow for bulk-loading historical bank and credit card statements with format detection, deduplication, and post-import baseline calculation
version: 1.0.0
---

# Historical Data Import Skill

This skill provides a comprehensive workflow for importing historical transaction data from bank and credit card statements in CSV format, with automatic format detection, deduplication, and baseline spending profile computation.

## Purpose

Enable users to quickly load historical financial data from multiple institutions (Chase, Bank of America, American Express, etc.), establish baseline spending profiles, and unlock advanced features (anomaly detection, budget forecasting, spending trends).

## Prerequisites

Before starting historical data import:
1. User has CSV export(s) from one or more financial institutions
2. CSV files follow standard bank export format (bank name, date, amount, merchant, etc.)
3. User has access to `bulk_record_transactions()` MCP tool
4. Optional: User has spending baseline from other imports (for comparison)
5. Storage quota is available (large imports may take time)

## Import Workflow

### 1. File Preparation

Ask user to provide files:

```
📋 Historical Data Import Workflow

I can import transactions from CSV exports of your:
• Bank statements (checking, savings)
• Credit card statements (all card types)
• Investment account statements
• Other financial institutions

Supported formats:
✅ Chase (CSV, QFX)
✅ Bank of America
✅ American Express
✅ Wells Fargo
✅ Discover
✅ Capital One
✅ Most US banks (auto-detect format)

What would you like to import?
1. Single file
2. Multiple files (batch import)
3. Folder of statements (auto-discover)

Please provide the CSV file(s) and specify the institution (if not auto-detectable).
```

### 2. Format Detection

For each file provided, auto-detect the format:

```python
# Pseudo-code for format detection
formats_to_try = [
    "chase_csv",
    "bofa_csv",
    "amex_csv",
    "generic_csv"
]

for fmt in formats_to_try:
    try:
        parsed = parse_csv(file, format=fmt)
        if validate(parsed) and confidence(parsed) > 0.8:
            return fmt
    except:
        continue

# If no format matches, prompt user
return prompt_user_for_format()
```

Example detection:

```
📂 Uploaded: chase_checking_2024.csv

Analysis:
- Columns detected: Date, Description, Amount, Balance
- First row: "01/15/2024, WHOLE FOODS MARKET, -45.32, 1234.56"
- Format confidence: 97%
- Inferred format: Chase Checking Account (CSV)

Is this correct?
[✅ Yes, continue / ❌ Wrong format / 🤔 Not sure]
```

### 3. Parsing and Validation

Parse the file and validate data:

```
📊 Parsing Results for chase_checking_2024.csv

Summary:
- Total rows: 156
- Parsed successfully: 154
- Errors/warnings: 2

Sample transactions:
1. 2024-01-15 | WHOLE_FOODS_MARKET | -45.32 USD | groceries
2. 2024-01-16 | SHELL_GAS_STATION | -32.00 USD | transport
3. 2024-01-18 | NETFLIX_SUBSCRIPTION | -15.49 USD | subscriptions

Detected categories:
- Groceries: 12 transactions
- Transport: 8 transactions
- Subscriptions: 3 transactions
- Dining: 10 transactions
- Other: 121 transactions

Errors (2):
- Row 45: Missing amount field
- Row 112: Invalid date format (2024-13-45)

Next step: Import these transactions?
[✅ Continue / 🔧 Adjust mapping / ❌ Cancel]
```

### 4. Deduplication Check

Before import, check for duplicates within the file and against existing data:

```
🔍 Deduplication Check

Checking against existing transactions:

Potential duplicates found (5):
1. NETFLIX subscription (2024-01-18, $15.49) — matches existing
   → Recommend: Skip
2. WHOLE_FOODS (2024-02-01, $45.32) — similar to existing
   → Confidence: 45% (different store location?)
   → Recommend: Import (different location)

Duplicates within file (0):
- All transactions in this file are unique

Action:
- Skip: 1 transaction (exact NETFLIX match)
- Import: 153 transactions

Total to import: 153/154 (99.4% unique)

Ready to proceed?
[✅ Yes, import / 🔧 Review duplicates / ❌ Cancel]
```

### 5. Bulk Import

Execute the import:

```
⏳ Importing 153 transactions...

Progress:
[████████████████░░░░░░░░░░░░░░░░] 40% (60/153)

Processing...
- Batch 1 (rows 1-50): ✅ Complete
- Batch 2 (rows 51-100): ✅ Complete
- Batch 3 (rows 101-153): ⏳ In progress...

Time elapsed: 12 seconds
Estimated time remaining: 5 seconds
```

### 6. Import Summary

Show results:

```
✅ Import Complete!

Summary:
- Total processed: 154
- Successfully imported: 153
- Skipped (duplicates): 1
- Errors: 0

Data span:
- Date range: 2024-01-15 to 2024-12-28
- Total transactions: 153
- Total amount: $12,847.32
- Average per transaction: $83.97

Top merchants:
1. WHOLE_FOODS_MARKET: 12 transactions, $540.00
2. SHELL_GAS_STATION: 8 transactions, $256.00
3. NETFLIX_SUBSCRIPTION: 12 transactions, $185.88
4. TRADER_JOES: 9 transactions, $378.50
5. [Other]: 112 transactions, $11,486.94

Categories discovered:
- Uncategorized: 89 transactions
- Groceries: 21 transactions
- Transport: 8 transactions
- Subscriptions: 15 transactions
- Dining: 20 transactions

Next steps:
1. Review uncategorized transactions (89 remaining)
2. Train category inference model
3. Compute baseline spending profile
4. Enable anomaly detection

Would you like to:
• Review and categorize uncategorized transactions
• Run baseline computation now
• Import more files
• Finish
```

### 7. Uncategorized Transaction Review

For transactions without a category:

```
📂 Reviewing Uncategorized Transactions (89 total)

Showing sample unmapped transactions:
1. AMAZON_CHARGE_1A | $34.50 | Category: ???
   → Possible categories: Shopping, Electronics, Other
   → Infer: [Shopping / Electronics / Ask me / Skip]

2. TARGET_PURCHASE | $78.20 | Category: ???
   → Possible categories: Shopping, Groceries, Other
   → Infer: [Shopping / Groceries / Ask me / Skip]

3. UBER_TRIP | $18.75 | Category: ???
   → Possible categories: Transport, Dining, Other
   → Infer: [Transport / Ask me / Skip]

Batch action:
• Apply guessed categories to similar merchants (Recommended)
• Manually review all 89
• Skip categorization for now

Recommendation: Use inferred categories (high confidence), then manually review outliers.
```

### 8. Baseline Computation

After import, compute baseline spending:

```
📊 Computing Baseline Spending Profile...

Analysis period: 2024-01-15 to 2024-12-28 (348 days)

Computing metrics:
- Monthly baseline per category
- Seasonal adjustments
- Anomaly thresholds (mean ± 2σ)
- Recurring transaction patterns

Results:

Monthly Baseline (2024 average):
- Groceries: $180.00 ± $32.00
- Dining: $210.00 ± $48.00
- Transport: $85.00 ± $21.00
- Subscriptions: $120.00 ± $0 (fixed)
- Shopping: $220.00 ± $95.00
- Utilities: $150.00 ± $18.00
- Entertainment: $80.00 ± $35.00
- Other: $312.00 ± $120.00

Total Monthly: $1,337.00 ± $180.00

Seasonal patterns detected:
- Heating costs spike in winter (utilities +$40/month in Dec-Feb)
- Summer travel (dining +$30/month in Jun-Aug)
- Holiday shopping (shopping +$150/month in Nov-Dec)

Anomaly thresholds configured:
- Groceries: Alert if > $244/month (mean + 2σ)
- Dining: Alert if > $306/month (mean + 2σ)
- Shopping: Alert if > $410/month (mean + 2σ)

✅ Baseline ready! Anomaly detection enabled.
```

### 9. Import Complete

Final summary:

```
# Historical Data Import Complete

✅ Successfully imported 153 transactions spanning 12 months
✅ Baseline spending profile computed
✅ Anomaly detection ready to go

**What you can now do:**
1. Get daily anomaly digests (start tomorrow)
2. Set and track budgets against your baseline
3. Forecast next month's spending
4. Review spending trends over time
5. Identify recurring subscriptions and recurring payments

**Recommended next steps:**
1. Review the 89 uncategorized transactions (quick 5-min task)
2. Set budgets for each spending category (use your baseline as a starting point)
3. Start receiving daily anomaly digests
4. Track subscriptions manually or via renewal alerts

Ready to get started?
[Set budgets now / Review categories / Skip for now]
```

## Multi-File Batch Import

For importing multiple files:

```
📂 Batch Import — Multiple Files

You've selected 3 files:
1. chase_checking_2024.csv (153 transactions)
2. amex_platinum_2024.csv (87 transactions)
3. bofa_savings_2024.csv (32 transactions)

Total: 272 transactions

Process:
1. Detect format for each file ✅
2. Parse and validate ✅
3. Check for duplicates across files ✅
4. Deduplicate against existing data ✅
5. Import all files sequentially
6. Compute combined baseline
7. Generate report

Proceed?
[✅ Yes / ❌ Cancel]
```

## Error Handling

- **Invalid format**: "I couldn't parse this file. Please specify the format (Chase, Bank of America, etc.) or provide a sample row."
- **Missing columns**: "The file is missing critical columns (Date, Amount). Please check the export format."
- **Date parsing fails**: "I couldn't parse the date format. Provide samples and I'll adjust."
- **Duplicate overload**: "This file appears to be 80% duplicates. Check for multiple exports of the same period."
- **Empty file**: "The file has no transactions. Check the export date range."

## Example Scenarios

### Example 1: Single Chase Checking Import

```
User: "Import my 2024 Chase checking statements"
Bot: Detects Chase format, parses 156 transactions
Bot: Finds 3 duplicates (matches existing data)
Bot: Imports 153 transactions successfully
Bot: Computes baseline from 12-month history
Result: User now has full 2024 baseline and anomaly detection
Time: ~30 seconds
```

### Example 2: Multi-Account Batch Import

```
User: "Import checking, savings, and credit card"
Bot: Detects 3 different formats
Bot: Parses all 3 files (Chase, BoA, Amex)
Bot: Deduplicates across files
Bot: Imports 412 unique transactions
Bot: Computes consolidated baseline across all accounts
Result: Complete financial picture with cross-account anomaly detection
Time: ~2 minutes
```

### Example 3: Import with Manual Review

```
User: "Import but let me categorize transactions"
Bot: Imports transactions
Bot: Flags 89 uncategorized
Bot: User spends 5 minutes assigning categories
Bot: System updates categories
Bot: Recomputes baseline with corrected categories
Result: More accurate anomaly detection and spending insights
Time: ~10 minutes
```

## Version History

- v1.0.0 (2026-03-26): Initial stub for multi-format CSV import with format detection, deduplication, and baseline computation
