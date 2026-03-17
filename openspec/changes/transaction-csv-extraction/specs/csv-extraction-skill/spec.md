# CSV Extraction Skill

## Purpose
LLM-driven adaptive CSV parsing skill for the finance butler. Guides the runtime through a script-generation workflow that handles arbitrary CSV layouts without loading the file into context or calling per-row tools.

## ADDED Requirements

### Requirement: CSV extraction skill definition
The finance butler MUST have a `transaction-csv-extraction` skill in `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md` that guides the LLM through adaptive CSV import.

#### Scenario: Skill discovery
- **WHEN** the finance butler's runtime session activates the `transaction-csv-extraction` skill
- **THEN** the skill MUST be loadable from `roster/finance/.agents/skills/transaction-csv-extraction/SKILL.md`
- **AND** the skill MUST have valid frontmatter with `name`, `description`, and `version` fields

#### Scenario: Skill activation trigger
- **WHEN** a user provides a CSV file path and requests transaction import
- **THEN** the butler MUST activate the `transaction-csv-extraction` skill to guide the workflow
- **AND** the skill MUST accept a file path and an optional account identifier as inputs

### Requirement: Script-generation workflow
The skill MUST instruct the LLM to follow a write-script-then-execute workflow. The LLM MUST NOT read the full CSV into its context or call single-record tools in a loop.

#### Scenario: CSV sampling phase
- **WHEN** the skill workflow begins with a CSV file path
- **THEN** the LLM MUST read only the first 10–15 lines of the file (header + sample rows)
- **AND** the LLM MUST infer the column mapping from the sample: which column is date, merchant/description, amount (single vs split debit/credit columns), and any other available fields
- **AND** the LLM MUST infer the date format, amount sign convention, and CSV delimiter from the sample

#### Scenario: Script generation phase
- **WHEN** the LLM has inferred the column mapping from the sample
- **THEN** the LLM MUST write a self-contained Python script that:
  - Uses only Python stdlib modules (`csv`, `json`, `urllib.request`, `datetime`, `hashlib`, `sys`)
  - Reads the full CSV file from disk
  - Maps each row to the normalized transaction schema (posted_at, merchant, amount, currency, category, description)
  - POSTs transactions in batches of 100 to the bulk ingestion endpoint (`POST /api/finance/transactions/bulk`)
  - Handles BOM markers, quoted fields, and empty trailing rows
  - Prints a JSON summary to stdout: `{"total": N, "imported": N, "skipped": N, "errors": N}`
  - Exits with code 0 on success, non-zero on fatal errors
- **AND** the script MUST NOT depend on `requests`, `pandas`, or any non-stdlib package

#### Scenario: Script execution and self-correction
- **WHEN** the LLM executes the generated script
- **AND** the script exits with a non-zero code or prints errors to stderr
- **THEN** the LLM MUST read the error output, diagnose the issue, modify the script, and re-execute
- **AND** the LLM MUST attempt at most 3 self-correction cycles before reporting failure to the user

#### Scenario: Result reporting
- **WHEN** the script completes successfully
- **THEN** the LLM MUST parse the JSON summary from stdout
- **AND** the LLM MUST report to the user: total rows processed, rows imported, rows skipped (with explanation that these are duplicates), and any errors
- **AND** if `skipped` count exceeds 20% of `total`, the LLM MUST warn the user that some transactions may have been deduplicated and suggest verification

### Requirement: Token budget protection
The skill MUST prevent the LLM from consuming excessive tokens during CSV import. This is a hard constraint, not a suggestion.

#### Scenario: Prohibition on full-file context loading
- **WHEN** the `transaction-csv-extraction` skill is active
- **THEN** the skill prompt MUST contain an explicit, unambiguous instruction that the LLM MUST NOT read the entire CSV file into its context window
- **AND** the skill prompt MUST state the reason: "Reading a full CSV into context would consume hundreds of thousands of tokens and is not an acceptable approach"
- **AND** the maximum number of CSV lines the LLM may read into context MUST be 15 (header + sample rows)

#### Scenario: Prohibition on per-row tool calls
- **WHEN** the `transaction-csv-extraction` skill is active
- **THEN** the skill prompt MUST contain an explicit, unambiguous instruction that the LLM MUST NOT call `record_transaction`, `record_transaction_fact`, or any single-record ingestion tool in a loop
- **AND** the skill prompt MUST state the reason: "Calling a tool per CSV row would generate thousands of MCP round-trips and consume an unacceptable number of tokens"
- **AND** the ONLY acceptable ingestion path MUST be the bulk HTTP endpoint via a generated script

#### Scenario: Skill prompt emphasis
- **WHEN** the skill prompt is authored
- **THEN** the token budget constraints MUST appear in a prominently placed section (e.g., "CRITICAL CONSTRAINTS" or equivalent) near the top of the skill document, before the workflow steps
- **AND** the constraints MUST be stated in imperative form (MUST NOT, NEVER) not advisory form (should avoid, prefer not to)

### Requirement: API connectivity verification
The generated script MUST verify that the bulk endpoint is reachable before processing CSV rows.

#### Scenario: Pre-flight connectivity check
- **WHEN** the generated script starts execution
- **THEN** it MUST send a HEAD or GET request to the dashboard API base URL before processing any CSV rows
- **AND** if the API is unreachable, the script MUST exit with a clear error message ("Dashboard API not reachable at <URL>") and non-zero exit code
- **AND** no transactions MUST be partially imported if the connectivity check fails

### Requirement: Dashboard API URL discovery
The skill MUST provide the LLM with a reliable way to determine the dashboard API base URL.

#### Scenario: URL construction
- **WHEN** the LLM generates the import script
- **THEN** the script MUST use `http://localhost:8000` as the default dashboard API base URL
- **AND** the script MUST accept an optional `--api-url` command-line argument to override the default
- **AND** the skill prompt MUST instruct the LLM to check for a `BUTLERS_API_URL` environment variable as a secondary override
