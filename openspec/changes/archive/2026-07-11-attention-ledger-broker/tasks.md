# Tasks — attention-ledger-broker (bu-qvnce.8, slices 1-2)

## 1. Attention ledger + seeded quiet hours (spec: core-notify, proactive-insight-engine)

- [x] 1.1 Migration `core_160_attention_ledger.py`: `public.attention_ledger`
  table (outcome/source/priority_score CHECK constraints, indexes for
  recent-first listing, outcome counting, dedup_key correlation) + grants to
  all runtime roles.
- [x] 1.2 Migration: guarded data-only seed of `public.approvals_policy` and
  `public.insight_settings` to 23:00-08:00 Asia/Singapore, only when
  currently unconfigured (idempotent — never overwrites an owner's own
  config).
- [x] 1.3 `src/butlers/core/attention_ledger.py`: `record_attention_event()`
  (best-effort writer), `normalize_priority()` /`is_priority_urgent()`
  (cross-path 1-100 scale), `count_attention_events_since()` (notify-path
  counting), `get_suppressing_context_signal()` (deterministic dnd/sleeping
  read).

## 2. notify() boundary wiring (spec: core-notify)

- [x] 2.1 Ledger row on delivery_preferences-based defer.
- [x] 2.2 Context-bus consult added to the approvals_policy quiet-hours gate
  (short-circuited: only queried when quiet hours did not already
  suppress); ledger row on suppression from either source, tagged with the
  specific reason (`quiet_hours` vs `context_bus:<signal>`).
- [x] 2.3 Ledger row (`outcome=delivered`) on both successful-delivery exit
  paths (switchboard self-delivery, and the general switchboard-client
  path), capturing `notification_id` as `notification_ref` when present.
- [x] 2.4 Tests: `tests/daemon/test_notify_attention_ledger.py` (quiet-hours
  suppress + ledger row, context-bus suppress + ledger row, priority="high"
  bypasses both, successful delivery records `delivered`).

## 3. delivery_cycle() boundary wiring (spec: proactive-insight-engine)

- [x] 3.1 Priority-urgent bypass: when quiet hours or the context bus would
  suppress the whole cycle, check for any pending candidate at/above
  `URGENT_PRIORITY_THRESHOLD` (90) first; if none, fully suppress (one
  ledger row, `outcome=suppressed`) as before; if any, narrow the cycle's
  working set to urgent candidates only — routine candidates stay
  `pending` untouched.
- [x] 3.2 Ledger rows on delivery: `delivered` for a single-candidate
  delivery, `coalesced` (one row per candidate) for a digest.
- [x] 3.3 Tests: `tests/modules/test_insight_attention_ledger.py`
  (urgent-bypasses-quiet-hours + routine-stays-pending, fully-suppressed
  when no urgent candidate, dnd-signal suppression + urgent bypass, no
  signal delivers normally) and
  `tests/integration/test_attention_ledger_roundtrip.py` (real-Postgres:
  table shape/constraints, seeded defaults, seed idempotency,
  `record_attention_event` round-trip via the real production writer).

## 4. Pure-helper coverage (spec: core-notify, proactive-insight-engine)

- [x] 4.1 `tests/core/test_attention_ledger.py`: `normalize_priority`
  (label/int/numeric-string/bool/None/out-of-range),
  `is_priority_urgent`, `record_attention_event` (fail-open on bad
  pool/invalid enum/DB error), `count_attention_events_since`
  (zero-filled outcomes, fail-open), `get_suppressing_context_signal`
  (dnd detected, non-suppressing signal ignored, fail-open on error).

## 5. Documentation

- [x] 5.1 RFC 0011 Amendment 1: ledger schema, seeded-defaults policy,
  context-bus integration (closes the "optional, deferred to a follow-up"
  note in RFC 0011's Integration section), priority-urgent bypass, dedup/
  cross-fleet correlation via `dedup_key` on the ledger.
- [x] 5.2 `openspec` spec deltas for `core-notify` and
  `proactive-insight-engine` (this change).

## 5b. Attention ledger reader + Trust Console panel (bu-tdd4k.4, slice 5)

- [x] 5b.1 `GET /api/attention/ledger`: windowed (`since`/`until`), filterable
  (`intent`/`source`/`outcome`/`origin_butler`) paginated row list, newest
  first. Degraded envelope (`source_available=false`) on an unreachable
  pool; a true empty/unmigrated-table result stays `source_available=true`.
- [x] 5b.2 `GET /api/attention/ledger/summary`: per-`origin_butler`
  delivered/coalesced/deferred/suppressed/total rollup over a window
  (defaults to the last 7 days), with a `suppressed_never_delivered` flag
  (`suppressed > 0 AND delivered == 0`) -- the exact live failure this epic
  fixed for secrets_lifecycle (bu-tdd4k.2: 120 suppressed / 0 delivered).
- [x] 5b.3 Trust Console (`ApprovalsPage.tsx`) panel rendering the summary,
  with flagged sources surfaced loudly (not folded into the calm case).
- [x] 5b.4 Tests: `tests/api/test_attention_ledger.py` (mocked-pool --
  degraded envelope, filter threading, suppressed_never_delivered), real-
  Postgres additions to `tests/integration/test_attention_ledger_roundtrip.py`
  (the actual GROUP BY/FILTER SQL, windowing), frontend vitest for the panel.
- [x] 5b.5 `openspec` spec delta: `core-notify` gains the "Attention Ledger
  Reader" requirement (this file's sibling `specs/core-notify/spec.md`).

## 6. Close-out

- [x] 6.1 `openspec validate attention-ledger-broker --strict`
- [x] 6.2 Archive on merge; update `core-notify` / `proactive-insight-engine`
  main specs.

## Deferred (proposed as follow-up beads, not half-implemented here)

- Slice 3: convert finance's direct-notify prompt-cron tasks to insight
  candidates (existing `run_insight_scan` job already covers overlapping but
  not identical ground — cadence and richness differ per task; needs its
  own design pass, not a quick toml flip).
- ~~Slice 4: same-window coalescing of multiple notify()-path sends + an
  hourly urgent sub-cycle (priority>=90 as "hours, not one daily slot").~~
  Delivered via bu-o8233 — see `openspec/changes/attention-ledger-coalescing-urgent-subcycle`
  and RFC 0011 Amendment 2.
- ~~Slice 5: dashboard attention-ledger panel under Trust Console.~~
  Delivered via bu-tdd4k.4 — see section 5b above.
