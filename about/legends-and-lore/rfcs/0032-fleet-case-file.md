# RFC 0032: Fleet Case File

**Status:** Draft (Slices 1-2 landed — schema + read API)
**Date:** 2026-09-05

## Context

The Switchboard insight broker already computes multi-butler correlated
clusters and pays an LLM call to synthesize a one-sentence summary for each
cluster (`roster/switchboard/tools/insight/broker.py::_cluster_candidates`,
`_synthesize_cluster_sentence`) — then discards both every delivery cycle.
Two structural gaps follow directly from that:

- Correlation is scoped to one cycle's top-B candidates, so a situation that
  unfolds over several days (a multi-day illness, a slow-building financial
  problem) is invisible to clustering even though every contributing signal
  was individually noticed.
- The urgent bypass (RFC 0011 §quiet-hours override) is evaluated per
  candidate, not per situation. One illness noticed independently by five
  butlers breaks quiet hours five times instead of once.

`src/butlers/core/domain_event_reactions.EVIDENCE_KINDS` — the shared
evidence-kind vocabulary for typed domain-event evidence — has no `case`
term, confirming there is currently no durable object a butler can attach
situation evidence to.

## Decision

Introduce `public.fleet_cases` as the durable object for "one situation,
one case": a correlated cluster becomes a case the first time it is
recognized, and further evidence accretes onto the same case instead of
re-deriving the cluster from scratch every cycle.

A case has:

- `correlation_key` — a readable key identifying the situation (for example
  `health:owner:respiratory-illness`). Not free-form UUID noise: readable so
  an operator can recognize a case from the key alone.
- `state` — `open | watching | closing | closed`. Only `closed` is terminal.
- `posture` — `silent | routine | active | urgent`. Contributors will
  eventually propose a posture; the Switchboard arbitrates the case's actual
  posture (posture arbitration ships in a later slice — see Slice plan).
- `outcome` — required exactly when `state = 'closed'`, forbidden otherwise
  (`chk_fleet_cases_closed_needs_outcome`). A lapse sweep (later slice) closes
  a case by writing `outcome = 'lapsed'`; it is a value of `outcome`, not a
  fifth `state`.

At most one non-closed case may exist per `correlation_key`
(`uq_fleet_cases_active_correlation_key`, a partial unique index on
`state <> 'closed'`) — the DB-level backstop against two butlers racing to
open the same situation twice.

`public.fleet_case_evidence` records one contribution per contributor per
case. Idempotence is a table constraint, not application logic:
`UNIQUE(case_id, contributor, kind, ref)` — the same contributor reporting
the same `(kind, ref)` again is a no-op at the database, not a
best-effort application-level dedup.

`public.fleet_case_links` binds a case to an entry in another ledger (an
insight candidate, an owner condition, a runtime-attention record, or
another case) by `(case_id, link_kind, ref)`, uniquely. This is the seam a
later slice's three-ledger binding (see Slice plan, S7) writes through; no
binding logic ships in this slice.

### Write authority

- `fleet_case_evidence`: any butler role may INSERT (contributors report
  evidence for a situation they observed). Rows are never updated — a
  correction is a new evidence row, not a mutation.
- `fleet_cases` and `fleet_case_links`: only `butler_switchboard_rw` may
  INSERT or UPDATE. The Switchboard is the sole arbiter of a case's
  existence, state, posture, and ledger bindings; every other role's plain
  GRANT-level write access to these two tables is closed by row-level
  security policies keyed on `current_user`, not by GRANT/REVOKE alone —
  `scripts/init-db.sql` re-widens default privileges on every rerun, so a
  bare REVOKE would not survive a bootstrap re-run (see the "Fencing a
  `public` table to one runtime role" note in `AGENTS.md`, and
  `core_210_expected_signals.py` for the same pattern applied to a
  differently-shaped ledger). All roles retain SELECT on both tables — a
  case file is a shared read surface even though only the Switchboard
  writes it.

## Slice plan

This RFC is written for the whole feature; only Slice 1 has landed.

- **S1 (landed):** `public.fleet_cases`, `public.fleet_case_evidence`,
  `public.fleet_case_links` — schema, constraints, grants/RLS only. No
  broker wiring, no MCP tools, no dashboard surface.
- **S2 (this change):** read API — `GET /api/switchboard/cases` (cursor-
  paginated list) and `GET /api/switchboard/cases/{case_id}` (one case with
  its evidence and links), hosted on the Switchboard API surface. No
  dashboard frontend page ships in this slice — read-only API only.
- **S3:** contribution tools — `find_open_case`, `open_case`,
  `contribute_case_evidence`, `propose_case_posture`, `close_case`,
  `read_case`. Adds `case` to `EVIDENCE_KINDS`.
- **S4:** situation-scoped attention — one urgent bypass per case per
  quiet-hours window, keyed by case rather than by candidate.
- **S5:** lapse sweep — may only transition a case to `closed` with
  `outcome = 'lapsed'`; never resurrects.
- **S6:** backfill — creates only `closed`/`lapsed` historical cases from
  existing data, never resurrects an inferred case as open.
- **S7:** three-ledger binding through `fleet_case_links`.

## Non-goals

No joint-objective register. No `delegate_act` mandates — dropped from this
design; commitments/mandates remain the province of RFC 0026. Backfill
(S6) never resurrects a case as active — it only writes historical
`closed`/`lapsed` rows.

## Alternatives rejected

- Extending `public.insight_candidates` with a cluster/parent pointer
  instead of a new table: candidates are cycle-scoped and expire; a
  situation that spans cycles needs a lifecycle (state, posture, outcome)
  candidates were never designed to carry.
- Enforcing switchboard-only writes with GRANT/REVOKE alone: does not
  survive an `init-db.sql` rerun, which re-widens default privileges on
  every `public` table the migration user creates.
