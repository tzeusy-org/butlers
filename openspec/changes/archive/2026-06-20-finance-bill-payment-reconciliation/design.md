# Design — finance-bill-payment-reconciliation

## Storage truth (as-built, before this change)

- **Bills** live in `finance.bills` (raw SQL in `tools/bills.py:track_bill` /
  `upcoming_bills`). Columns include `payee, amount NUMERIC(14,2) NOT NULL,
  currency, due_date, status, payment_method, account_id, paid_at, metadata`.
- **Transactions** live in `finance.transactions` (primary) with a
  fire-and-forget SPO mirror to `public.facts`
  (`predicate='transaction_{direction}'`). Columns include `merchant, amount,
  currency, direction ('debit'|'credit'), posted_at, payment_method,
  account_id, metadata`.
- **No link** between the two tables.
- A separate **registered** `track_bill_fact` MCP tool (`@_tool("facts")` in
  `modules/tools.py`, implemented in `tools/facts.py`, exported via
  `tools/__init__.py` + `modules/__init__.py`, with a dedicated test class in
  `tests/test_facts.py`) writes bills to SPO `predicate='bill'`. It is **not**
  consumed by `upcoming_bills` (which reads the table), but it is a live tool
  with tests — removing it is a real, scoped deletion, not dead-code removal.

This change builds reconciliation against the live primary tables.

## Matching algorithm (deterministic, lives in tool code)

Input: one debit transaction `T`, or a batch scan. Candidate bills `B` are
`finance.bills` rows with `status IN ('pending','overdue')` and
`reconciled_transaction_id IS NULL`.

A bill `b` is a **candidate** for `T` when **all** hold:

1. **Payee↔merchant match** — normalized, case-insensitive comparison of
   `b.payee` against `T.merchant` (and `T.metadata->>'normalized_merchant'` when
   present). Match if equal after normalization, or one contains the other as a
   whole-token substring. (Reuse merchant-normalization conventions already in
   the finance domain.)
2. **Currency match** — `b.currency = T.currency`.
3. **Date window** — `(T.posted_at AT TIME ZONE 'UTC')::date` within
   `[b.due_date - LOOKBACK, b.due_date + GRACE]`. The truncation timezone is
   **UTC**, fixed, so window-boundary tests are deterministic regardless of
   session timezone. Defaults: `LOOKBACK = 45 days` (bills are commonly paid well
   before the printed due date, e.g. statement→pay), `GRACE = 7 days`. When
   `b.statement_period_end` is set, the window may anchor on it instead.
4. **Amount compatibility**, one of:
   - `b.amount = 0.00` → **placeholder**; amount unknown, compatible by
     definition (the transaction backfills it).
   - `b.amount > 0` → `abs(T.amount)` within tolerance of `b.amount`:
     `max($1.00, 1% of b.amount)`.

### Confidence classification

| Tier | Condition | Action |
|------|-----------|--------|
| **auto_settle** | Exactly **one** candidate for `T`, AND (exact amount within tolerance **OR** placeholder `$0` bill), AND payee is an exact normalized match | Settle automatically |
| **confirm** | ≥1 candidate but: multiple candidates, OR fuzzy (substring) payee match, OR amount present but outside tolerance yet plausible | Surface for user confirmation |
| **none** | No candidate | No-op |

Only `debit` transactions are matched (credits/refunds never settle a bill).

### Settlement action (auto_settle)

```sql
UPDATE finance.bills SET
  status                  = 'paid',
  amount                  = CASE WHEN amount = 0 THEN @txn_abs_amount ELSE amount END,
  paid_at                 = @txn_posted_at,
  payment_method          = COALESCE(payment_method, @txn_payment_method),
  reconciled_transaction_id = @txn_id,
  metadata                = metadata || jsonb_build_object(
                              'reconciled_at', now(),
                              'reconciliation', 'auto'),
  updated_at              = now()
WHERE id = @bill_id
  AND status <> 'paid'
  AND reconciled_transaction_id IS NULL;
```

The `WHERE` guard makes settlement idempotent **structurally**, not just via the
pre-scan: two near-simultaneous settlements (inline hook + a manual
`reconcile_bills`) cannot both win. Callers MUST check the affected row count and
treat a zero-row update as "already settled by someone else", not an error.

The same `track_bill` SPO mirror (added by this change) fires after settlement so
the fact layer reflects `status=paid`.

### Idempotency & safety

- A transaction whose id already appears as some bill's
  `reconciled_transaction_id` is skipped.
- A bill with `status='paid'` or non-null `reconciled_transaction_id` is never
  re-settled.
- Auto-settle requires a **single** candidate — any ambiguity downgrades to
  `confirm`. This is the guard against silently closing the wrong bill.

## Three invocation surfaces

1. **`reconcile_bills(lookback_days=90, payee=None)` MCP tool** — the primitive.
   Scans **bill→transaction**: it iterates unsettled pending/overdue bills and,
   for each, looks back over recorded debits in the trailing `lookback_days`
   (the outer scan horizon; the per-bill date window from the matcher,
   `LOOKBACK=45d`/`GRACE=7d`, still applies). This direction is what closes the
   **payment-recorded-before-the-bill-existed** case: the inline hook (surface 2)
   finds nothing because no bill exists yet, but the sweep later finds the
   already-recorded debit. Auto-settles `auto_settle` matches, returns
   `{auto_settled: [...], candidates: [...]}` where `candidates` are `confirm`
   tier with enough context for the LLM/user to choose. Idempotent.

2. **Post-`record_transaction` hook** — when a `debit` is inserted,
   `record_transaction` runs the matcher for *that* transaction only and includes
   a `bill_reconciliation` block in its response:
   - `{"auto_settled": {bill_id, payee, amount, paid_at}}` (high-confidence), or
   - `{"candidates": [{bill_id, payee, due_date, amount}, ...]}` (ambiguous), or
   - omitted/empty when no candidate.
   The matcher is synchronous pure-SQL; the SPO mirror stays fire-and-forget. No
   LLM in the daemon path (Rule 4).

3. **Scheduled sweep** — folded into `upcoming-bills-check` (weekly, already
   `dispatch_mode="prompt"`). The skill calls `reconcile_bills()` first, then
   reports auto-settled bills, ambiguous candidates, and still-unpaid past-due
   bills in its `notify()` digest.

### Edge cases

- **Duplicate / recurring same-payee bills.** `finance.bills` has **no** UNIQUE
  on `(payee, due_date)` (verified in `001_finance_tables.py`), and Example 6
  creates a `$0.00` placeholder on every "statement ready" email — so two pending
  rows for the same payee can coexist (e.g. a recurring monthly payee, or a
  placeholder plus a later amount-bearing row with a nearby due date). The
  matcher counts **all** candidates whose window contains the payment; >1 → the
  match is `confirm`, never `auto_settle`. When exactly one of several same-payee
  bills falls inside the window, auto-settle applies but MUST pick the bill whose
  anchor date (`statement_period_end` if set, else `due_date`) is closest to
  `posted_at`. Auto-settle never fires while two candidates remain in-window.
- **Partial payments / one bill across two transactions.** Out of scope for this
  change. A transaction materially below a **non-placeholder** bill amount (below
  tolerance) is `none`/`confirm`, never a partial auto-settle. A `$0.00`
  placeholder is backfilled by the single matched transaction and linked via the
  single-valued `reconciled_transaction_id`; splitting a bill across multiple
  settling transactions is not modeled.

## Spec drift resolution (bills storage)

Chosen direction: **table-primary + SPO mirror**, mirroring the transactions
contract — the lower-risk option (does not break `track_bill`/`upcoming_bills`)
and the consistent one.

- `butler-finance` spec requirement "Subscription and bill tools as property
  fact wrappers" is rewritten: bills are **primary in `finance.bills`**, with a
  fire-and-forget SPO mirror (`store_fact predicate='bill'`, `valid_at=NULL`) —
  paralleling the transactions requirement. `upcoming_bills` reads `finance.bills`
  (codifies reality).
- `track_bill` gains the SPO mirror it currently lacks (so memory/recall sees
  bills), writing the **same** metadata field set the spec requires (see the
  "Bill tools as dedicated table primary with SPO mirror" scenario — canonical
  list: `{payee, amount, currency, due_date, frequency, status, payment_method,
  account_id, paid_at, reconciled_transaction_id, source_message_id}`).
- The standalone `track_bill_fact` MCP tool is **removed** — this is a real,
  scoped deletion across: its `@_tool("facts")` registration in
  `modules/tools.py`, the `facts.py` implementation, the `tools/__init__.py` +
  `modules/__init__.py` exports, and its test class in `tests/test_facts.py`.
  The sibling `_fact` tools (`track_subscription_fact`, `track_account_fact`,
  `spending_summary_facts`) are **out of scope** and remain. If any skill/test
  consumes `track_bill_fact`, it is repointed to `track_bill` rather than
  deleted.

## Doctrine check

- **Rule 4 (deterministic daemon):** matching + auto-settle are pure SQL/Python
  in tool code — testable and predictable. The LLM only decides *ambiguous*
  cases, which is judgment, in a session. ✅
- **Rule 6 / finance manifesto ("never initiate payments"):** reconciliation
  records that a payment *already happened*; it never moves money. ✅
- **Trust boundary:** auto-settle is bounded (single candidate + amount/window);
  ambiguous stays manual; every auto-settle is logged with provenance and
  reported in the next digest, so it is reviewable. ✅

## Trade-offs considered

- **Bill-as-fact (full SPO migration) vs table-primary** — rejected full SPO
  migration for this change: it would break `track_bill`/`upcoming_bills` and
  couples a larger data-model move into a bug fix. Table-primary + mirror matches
  transactions and is reversible.
- **Pure scheduled sweep (no inline hook)** — rejected as the sole mechanism: it
  leaves a multi-day gap where "have I paid X?" answers wrong. The inline hook
  settles at payment time; the sweep is the backstop.
- **Auto-settle everything** — rejected (owner chose hybrid): unbounded fuzzy
  auto-matching risks closing the wrong bill silently.

## Observability

- `reconcile_bills` and the `record_transaction` hook log structured counts:
  `auto_settled`, `candidates`, `scanned`. The scheduled sweep's digest reports
  them to the owner. Per `craft-and-care/observability-and-operations.md`,
  reconciliation outcomes must be inspectable after the fact.
