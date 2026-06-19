# Tasks — finance-bill-payment-reconciliation

Sequencing/prioritization is owned by `/th-projects` project-direction; this is
the decomposition seed. TDD per `about/craft-and-care/testing-and-verification.md`:
the bug fix starts with a failing reproducer; new behavior gets tests at the tool
layer. Final gate: `ruff check` + `ruff format --check` + `make test-qg`.

## Track A — Schema + link (foundation)

- [ ] A1 — Finance schema migration `roster/finance/migrations/009_bills_reconciled_transaction_id.py`
  (raw-SQL `op.execute(...)` chain, NOT Alembic `src/butlers/migrations/versions/`;
  next number after `008_*`): add `finance.bills.reconciled_transaction_id UUID NULL`
  (document the reference to `finance.transactions.id`; no FK enforcement required).
  Additive/nullable; reversible downgrade. (spec: "Deterministic bill↔payment reconciliation")
- [ ] A2 — Add coverage in `tests/migrations/test_finance_migrations.py`: column
  present, nullable, default null; downgrade drops it.

## Track B — Deterministic matcher + reconcile_bills tool

- [ ] B1 — Reproducer test FIRST: a `$0.00 pending` HSBC bill + a matching HSBC
  debit → assert the bill is NOT settled today (red), will go green after B2/B3.
- [ ] B2 — Implement the deterministic matcher helper (payee normalization,
  currency, date window `LOOKBACK=45d`/`GRACE=7d`, amount tolerance
  `max($1, 1%)`, placeholder handling, confidence tiering) in
  `roster/finance/tools/bills.py` (or a new `reconciliation.py`). Pure SQL/Python.
- [ ] B3 — Implement `reconcile_bills(lookback_days=90, payee=None)`: scans
  **bill→transaction** (iterate unsettled bills, look back over recorded debits)
  so payments recorded before the bill existed are caught. Auto-settle
  high-confidence (backfill amount, paid_at, payment_method, link, provenance)
  with the guarded UPDATE (`WHERE status <> 'paid' AND reconciled_transaction_id
  IS NULL`, check row count). Return `{auto_settled, candidates}`. Idempotent.
- [ ] B4 — Register `reconcile_bills` as an MCP tool in
  `roster/finance/modules/tools.py` (group `"bills"`) with an LLM-explainable
  docstring.
- [ ] B5 — Matcher unit tests: high-confidence exact, placeholder backfill,
  multiple-candidate→confirm, fuzzy payee→confirm, amount-out-of-tolerance→confirm,
  currency mismatch→none, credit→none, window boundaries (UTC truncation),
  idempotency (linked txn skipped, paid bill not re-settled, guarded UPDATE
  yields zero rows on a second concurrent settle), same-payee duplicate bills
  (two in-window → confirm; one in-window → closest-anchor auto-settle), and
  payment-recorded-before-bill (sweep settles it).

## Track C — Settlement on payment (record_transaction hook)

- [ ] C1 — In `roster/finance/tools/transactions.py:record_transaction`, after the
  primary insert of a `debit`, run the single-transaction matcher and add a
  `bill_reconciliation` block (`auto_settled` | `candidates` | empty) to the
  response. Synchronous, in-process; SPO mirror stays fire-and-forget.
- [ ] C2 — Integration test: recording a debit that matches a placeholder bill
  returns `bill_reconciliation.auto_settled` and the bill is paid; an ambiguous
  case returns `candidates` and mutates nothing.

## Track D — Scheduled sweep

- [ ] D1 — Update `roster/finance/.claude/skills/upcoming-bills-check` to call
  `reconcile_bills()` first, then report auto-settled bills, ambiguous candidates,
  and still-unpaid past-due bills in the `notify()` digest.

## Track E — Bills storage spec-drift fix

- [ ] E1 — Add the fire-and-forget SPO mirror (`predicate='bill'`, `valid_at=NULL`)
  to `track_bill` in `roster/finance/tools/bills.py` (mirror metadata incl.
  `reconciled_transaction_id`); failure does not roll back the table upsert.
- [ ] E2 — Remove the standalone `track_bill_fact` tool (it is a live registered
  tool, not dead code): delete its `@_tool("facts")` registration in
  `roster/finance/modules/tools.py`, the implementation in
  `roster/finance/tools/facts.py`, the exports in `roster/finance/tools/__init__.py`
  and `roster/finance/modules/__init__.py`, and delete/rewrite its test class in
  `roster/finance/tests/test_facts.py`. Leave the sibling `_fact` tools
  (`track_subscription_fact`, `track_account_fact`, `spending_summary_facts`)
  untouched. If any skill consumes `track_bill_fact`, repoint it to `track_bill`.
- [ ] E3 — Tests: `track_bill` writes the table AND fires the SPO mirror;
  `upcoming_bills` reads the table; removed-tool path has no dangling references.

## Track F — Prompting fixes

- [ ] F1 — `roster/finance/AGENTS.md` Example 6: stop presenting `$0.00 pending`
  as terminal — label it a placeholder awaiting reconciliation; note the system
  will backfill+settle on the matching payment.
- [ ] F2 — `roster/finance/AGENTS.md`: add a behavioral guideline — on recording
  a debit, read `record_transaction`'s `bill_reconciliation`; affirm an
  auto-settled bill, confirm ambiguous candidates with the user; and the
  integrity rule: NEVER write settlement state into `metadata` prose without the
  structured `status` change.
- [ ] F3 — `bill-reminder` skill: reflect that bills may already be auto-settled;
  "mark as paid" remains the explicit/manual path for ambiguous cases.

## Track G — Spec finalization

- [ ] G1 — After implementation verifies the scenarios, run the OpenSpec archive
  flow to fold this change's deltas into `openspec/specs/butler-finance/spec.md`.
