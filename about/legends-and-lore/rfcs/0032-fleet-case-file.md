# RFC 0032: Fleet Case File

**Status:** Implemented (Slices 1-7 landed — schema, read API, contribution
tools, situation-scoped attention, lapse sweep, historical backfill,
three-ledger binding)
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
- `posture` — `silent | routine | active | urgent`. Contributors propose a
  posture via `propose_case_posture` (Slice 3); the Switchboard is the only
  role that can actually write it (RLS), so a proposal from any other butler
  is forwarded through Switchboard's `route()` primitive and takes effect as
  a plain last-write-wins update. A richer arbitration model (quorum, decay,
  per-butler cooldown) is not part of this design; "the Switchboard
  arbitrates" currently means "the Switchboard is the sole write authority,"
  not majority voting.
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

This RFC is written for the whole feature; all seven slices have landed.

- **S1 (landed):** `public.fleet_cases`, `public.fleet_case_evidence`,
  `public.fleet_case_links` — schema, constraints, grants/RLS only. No
  broker wiring, no MCP tools, no dashboard surface.
- **S2 (landed):** read API — `GET /api/switchboard/cases` (cursor-
  paginated list) and `GET /api/switchboard/cases/{case_id}` (one case with
  its evidence and links), hosted on the Switchboard API surface. No
  dashboard frontend page ships in this slice — read-only API only.
- **S3 (this change):** contribution tools — `find_open_case`, `open_case`,
  `contribute_case_evidence`, `propose_case_posture`, `close_case`,
  `read_case` (`src/butlers/core_tools/_fleet_cases.py`, gated behind the new
  `fleet_cases` core group). Adds `case` to `EVIDENCE_KINDS`. Still no broker
  wiring — the insight broker does not call these tools yet — and no
  dashboard write surface.
- **S4 (landed):** situation-scoped attention — one urgent bypass per case
  per quiet-hours window, keyed by case rather than by candidate
  (`fleet_cases.evaluate_case_attention`). Any number of
  `propose_case_posture`/`contribute_case_evidence` calls against the same
  `correlation_key` while a case is `posture='urgent'` collapse to at most one
  recorded bypass (a `public.attention_ledger` row, `dedup_key=` the case's
  correlation key) per quiet-hours window; outside quiet hours, or once the
  case steps down from urgent or closes, there is nothing to bypass. Still no
  broker wiring — the insight broker's own per-candidate bypass is untouched;
  this is the case-scoped primitive a later slice's three-ledger binding
  (S7) can connect it to.
- **S5 (landed):** lapse sweep (`fleet_cases.run_lapse_sweep`, registered as
  the Switchboard-owned scheduled job `fleet_case_lapse_sweep`, daily at
  04:10 UTC). Closes a case with `outcome = 'lapsed'` only when it is
  `posture` in `{silent, routine}`, `state <> 'closed'`, and has gone 7 days
  (`DEFAULT_LAPSE_STALENESS_WINDOW`) without a fresh `contribute_evidence`
  row or a `propose_case_posture`/`close_case` update. `active`/`urgent`
  cases are never auto-lapsed regardless of age, and the eligibility check
  and the write are one atomic `UPDATE`, so a case can never be resurrected
  and an already-closed case is never touched again.
- **S6 (landed):** backfill (`fleet_cases.backfill_historical_case`/
  `backfill_from_owner_conditions`, `scripts/backfill_fleet_cases.py` — a
  one-time/idempotent-rerun script, not a scheduled job). Source: resolved
  `public.owner_conditions` episodes (`butlers.core.owner_conditions`,
  pre-dates this RFC) — not the insight broker's clustering, which the
  Context section above already notes is discarded every delivery cycle and
  so has no durable history to backfill from. Each resolved episode becomes
  one `state='closed'` case keyed by
  `backfill:owner_condition:{source}:{fingerprint}:{episode}`, with
  `outcome` taken from the episode's `metadata.resolution_reason` (falling
  back to `"resolved"`). `backfill_historical_case` hard-codes
  `state = 'closed'` in its INSERT text — no caller can make it write an
  open case — and a `WHERE NOT EXISTS` guard on `correlation_key` makes
  reruns idempotent. No `fleet_case_links` row is written; that binding is
  S7's job.
- **S7 (landed):** three-ledger binding through `fleet_case_links`
  (`fleet_cases.write_case_link`, `core_tools._fleet_cases.record_case_link`).
  `link_kind` is one of `insight_candidate`, `owner_condition`,
  `attention_record` — `ref` is that ledger's own id
  (`public.insight_candidates.id`, `public.owner_conditions.id`,
  `public.attention_ledger.id` respectively). No new scheduled job: the write
  is triggered from the three existing call sites that can observe a genuine
  cross-ledger reference rather than a speculative correlation —
  `contribute_case_evidence` writes a link when called with one of the three
  reserved `kind` values (the insight-candidate and owner-condition paths;
  the insight broker itself still does not call any fleet-case tool, so an
  insight-candidate link requires some caller to cite the candidate id
  explicitly via evidence — no broker wiring ships in this slice either);
  `evaluate_case_attention`'s urgent-bypass path (Slice 4) writes an
  `attention_record` link for the `public.attention_ledger` row it just
  created, from both `contribute_case_evidence` and `propose_case_posture`;
  and `backfill_from_owner_conditions` (Slice 6) writes an `owner_condition`
  link back to the source episode for every case it touches, including a
  case an earlier (pre-Slice-7) run already created — a rerun repairs the
  missing links onto old rows, not just new ones. Write authority matches
  `fleet_cases` exactly (`butler_switchboard_rw` only, RLS): a caller on a
  non-Switchboard pool forwards through Switchboard's `route()`, mirroring
  Slice 3's `open_case`/`propose_case_posture`/`close_case`. Idempotent via
  `uq_fleet_case_links_ref`'s `(case_id, link_kind, ref)` uniqueness —
  `ON CONFLICT DO NOTHING` — the same shape as `contribute_evidence`.

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
