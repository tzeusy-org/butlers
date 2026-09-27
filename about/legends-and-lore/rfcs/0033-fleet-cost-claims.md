# RFC 0033: Fleet Cost Claims

**Status:** Implemented (Slices 1–3)
**Date:** 2026-09-15

## Context

Money known by one butler must sometimes be reconciled against evidence owned by
Finance. Direct sibling-schema access is forbidden, and an event cannot carry a
mutable claim lifecycle. Relationship loans also historically stored settlement
state in unconstrained fact metadata and inferred USD when currency was absent.

## Decision

`public.cost_claims` is a typed projection of a butler-owned source record.
Currency is required and never inferred. The stable `(asserted_by, claim_key)`
identity has at most one live row; amendments supersede rather than mutate the
assertion. `public.cost_claim_resolutions` holds Finance's verdict; a new assertion
has no resolution until Finance evaluates it. `public.cost_claim_events` is a
trigger-written audit trail.

Forced row-level security derives authority from `current_user`:

- an asserting runtime role may insert and lifecycle-update only its own claims;
- assertion fields are immutable after insert;
- only `butler_finance_rw` may write resolution verdicts;
- runtime roles cannot delete assertions, resolutions, or events;
- all runtime roles may read the public projection;
- no role receives access to another butler's schema.

The policies are the security boundary because `scripts/init-db.sql` deliberately
re-applies broad default DML grants to public tables. Replaying bootstrap therefore
must not widen effective claim or resolution authority.

Claims, resolutions, and events are durable application evidence and remain in the
nightly backup. The ordinary `pg_dump` never enables row-security: it carries these
tables' schema, ownership, policies, triggers, and fixed restore function, but omits
their table data. A separately scoped `psql` query, bound to the same exported
snapshot, then appends all three ledgers as hex-wrapped JSON staging rows. Before
either producer writes an artifact, a catalogue check in that same snapshot
requires each named table to retain FORCE RLS and exactly one
permissive, non-restrictive `PUBLIC USING (true)` `FOR SELECT` policy, with no
other policy that applies to SELECT (including `FOR ALL`). Any other included
forced-RLS relation still aborts ordinary `pg_dump`, and any changed claim read
policy aborts the scoped export, so neither path can silently publish filtered data.

Restore replays the staging rows through `cost_claim_restore_row()`, a fixed
`SECURITY DEFINER` function owned with the tables, executable only while assuming
that owner under the existing certified-restore membership precondition. It accepts
only the three named relations and suppresses only the trigger events that would
otherwise duplicate the historical event ledger. The artifact therefore preserves
row parity, FORCE RLS, ownership, and the existing definer fence without granting a
dump identity `BYPASSRLS` or opting the whole dump into row-security evaluation.

Relationship creates `loan:{fact_id}` claims only after its canonical fact is
durable. Projection failure cannot roll back the loan and is repaired by the
idempotent backfill. Settling a loan supersedes and replaces the fact in one
database transaction, then retracts a corresponding live claim.

Finance deterministically reconciles active claims under one advisory lock. It
matches direction, amount, counterparty, time window, and exact currency, excludes
transfers, and binds a transaction to at most one claim in
`finance.claim_match_bindings`. Each successful sweep atomically rebuilds those
current bindings from current evidence before choosing claims, so stale evidence
cannot reserve a transaction. It never performs FX conversion.

Verdicts distinguish:

- `unreconciled/no_candidate_in_window`: fresh coverage exists and no candidate did;
- `ambiguous`: multiple, cross-currency, or already-bound evidence forbids guessing;
- `unverifiable/no_account|never_synced|feed_stale`: Finance cannot truthfully decide.

`evidence_horizon_at` is the newest transaction evidence actually held, never the
sweep clock. A failed sweep retains the previous verdict and timestamp.

## Non-goals

No owner UI, approval verbs, insight/condition wiring, manifesto amendment,
shared-expense netting, FX settlement, or domain-event carrier ships in these
slices. The asserting schema remains the source of truth, so dropping the
projection never deletes the originating fact.
