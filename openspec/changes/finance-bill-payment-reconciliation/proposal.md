# finance-bill-payment-reconciliation

## Why

A real owner incident (2026-06-19): the owner paid an HSBC credit-card bill
(SGD 717.57 from UOB). The payment was recorded in `finance.transactions`, and a
separate bill row existed (`payee=HSBC CC`, `due_date=2026-06-19`), but the bill
stayed `status=pending` with `amount=0.00` — even though its own `metadata`
already read "HSBC CC bill paid". When the owner asked "have I paid my HSBC
bill?", the butler answered "not confirmed as paid yet."

Root cause, confirmed by code trace:

1. **No deterministic transaction↔bill link or reconciliation exists.**
   `finance.bills` and `finance.transactions` are independent tables with no
   `bill_id`/`reconciled_transaction_id` column, no join, and `record_transaction`
   has no post-write step that settles a matching pending bill
   (`roster/finance/tools/bills.py`, `roster/finance/tools/transactions.py`).
2. **The `$0.00 pending` row is *expected* output.** `roster/finance/AGENTS.md`
   Example 6 instructs the runtime, on an amount-less "statement ready" email, to
   call `track_bill(..., amount=<minimum if known or 0>, status="pending")`. The
   system then has no mechanism to ever reconcile that placeholder.
3. **Settlement is 100% LLM-driven and breaks across ephemeral sessions.** A bill
   only flips to paid when *some* session explicitly calls
   `track_bill(status="paid", ...)`. The payment alert and the statement email
   arrived as separate triggers → separate ephemeral sessions with no shared
   context. One session recognized the payment and wrote a prose breadcrumb into
   `metadata`, but never made the structured status change — the exact failure.

This is **doctrine-correct to fix as deterministic infrastructure**. Per
Non-Negotiable Rule 4 (vision.md), "the daemon is deterministic infrastructure;
intelligence is in ephemeral LLM sessions… mixing LLM logic into the daemon is a
defect." Deterministic payment↔bill matching is exactly the kind of testable,
predictable logic that should live in tool/daemon code, not be re-derived by an
LLM each session. Per the vision ("the owner's financial signals are maintained
without manual entry… the system is boring, it works"), reconciliation is core
to the finance butler's promise.

## What Changes

- **ADDED — deterministic reconciliation primitive (`reconcile_bills`).** A new
  finance MCP tool that scans pending/overdue bills against recent unlinked debit
  transactions and classifies each match as `auto_settle` (high-confidence),
  `confirm` (ambiguous), or none. It auto-settles high-confidence matches and
  returns ambiguous candidates for confirmation. Matching is pure SQL/Python — no
  LLM judgment in the daemon.

- **ADDED — settlement on payment (post-`record_transaction` hook).** When
  `record_transaction` records a `debit`, it runs the deterministic matcher for
  that transaction against pending bills and returns a `bill_reconciliation`
  block in its response: an `auto_settled` bill (high-confidence) or
  `candidates` (ambiguous). This closes the cross-session gap — the
  payment-recording session gets the candidate immediately.

- **ADDED — settlement policy (hybrid).** A match is auto-settled **only** when
  it is the *sole* candidate AND either (a) the transaction amount matches the
  bill amount within tolerance, or (b) the bill is a `$0.00` placeholder whose
  amount the transaction backfills, AND the payment falls within the bill's
  due-date window. Auto-settle backfills `amount`, sets `paid_at` from the
  transaction, sets `payment_method`, links the transaction, and records
  `reconciliation: "auto"` provenance. Multiple/fuzzy/amount-mismatched
  candidates are **surfaced for confirmation**, never auto-applied.

- **ADDED — scheduled reconciliation sweep.** A periodic pass (folded into the
  existing `upcoming-bills-check` schedule) runs `reconcile_bills` over stale
  pending/overdue bills, auto-settles high-confidence matches, and notifies the
  owner about ambiguous candidates and still-unpaid past-due bills. Catches
  payments recorded in a session that did not connect them (incl. legacy rows).

- **ADDED — explicit bill↔transaction link.** Migration adds
  `finance.bills.reconciled_transaction_id UUID NULL` (provenance + idempotency:
  a linked transaction is never re-matched; a paid bill is never re-settled).

- **MODIFIED — prompting fixes (`roster/finance/AGENTS.md`, `bill-reminder`).**
  Example 6 stops presenting `$0.00 pending` as a terminal state — it labels the
  placeholder as awaiting reconciliation. A new behavioral guideline requires the
  runtime to read `record_transaction`'s `bill_reconciliation` field and act on
  it, and **forbids writing settlement state into `metadata` prose without the
  corresponding structured `status` change** (the precise bug observed).

- **MODIFIED — resolve bills storage spec drift.** The spec currently says
  `track_bill` stores bills *as* SPO facts (`store_fact predicate='bill'`); the
  live code makes `finance.bills` the primary table and `upcoming_bills` reads
  that table, while a separate orphan `track_bill_fact` tool writes SPO. This
  change codifies reality (matching the transactions pattern): **`finance.bills`
  is primary, with a fire-and-forget SPO mirror**. `track_bill` gains the SPO
  mirror it lacks; the orphan `track_bill_fact` tool is removed (cruft cleanup)
  unless a consumer is found.

## Impact

- **Affected specs:** `butler-finance` (MODIFIED: bill storage + tool surface;
  ADDED: reconciliation requirements).
- **Affected code:** `roster/finance/tools/bills.py`,
  `roster/finance/tools/transactions.py`, `roster/finance/modules/tools.py`
  (tool registration), `roster/finance/tools/facts.py` +
  `tests/test_facts.py` (remove the `track_bill_fact` tool and its tests), a new
  finance-schema migration `roster/finance/migrations/009_*.py` for
  `reconciled_transaction_id`, `roster/finance/AGENTS.md`, and the
  `bill-reminder` / `upcoming-bills-check` skills.

**Out of scope:** partial payments / one bill settled across multiple
transactions (a sub-tolerance payment against a non-placeholder bill stays
`confirm`, never partial auto-settle); the broader bills-as-facts SPO migration
(this change codifies table-primary instead); reconciling credits/refunds.
- **Migration/rollback:** additive column (`reconciled_transaction_id`), nullable
  — backward compatible; rollback drops the column. No data backfill required;
  the scheduled sweep reconciles historical rows opportunistically.
- **Behavior change under users:** bills now move to `paid` automatically on a
  confident match. Risk of a wrong auto-match is bounded by the
  single-candidate + amount/window guard; ambiguous cases stay manual. The
  scheduled sweep's first run may settle a batch of historical placeholders —
  reported in the digest for review.

## Source References

- Non-Negotiable Rule 4 (deterministic daemon; LLM judgment in sessions only) —
  reconciliation matching is deterministic tool code, not LLM logic.
- Non-Negotiable Rule 6 (manifesto-binding) — finance manifesto: tracking,
  visibility, reminders; reconciliation records an already-made payment and does
  **not** initiate one, staying inside the "never initiate payments" boundary.
- Vision "What Success Looks Like" (financial signals maintained without manual
  entry; the system is boring and trusted with more autonomy).
