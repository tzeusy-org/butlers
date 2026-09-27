# Tasks — context-bus-producers

Implemented in a single PR under bead bu-hmdqz.15 (2026-07-12 JARVIS pursuit,
move 15/15).

## 1. Deterministic producers

- [x] 1.1 `src/butlers/jobs/context_producers.py`: calendar → meeting/focused
      (general), home → at_home from HA presence (home), travel → traveling from
      an underway trip (travel), health → sleeping from the quiet-hours window
      (health). Zero-LLM, idempotent upsert + clear-on-reverse, bounded TTL.
- [x] 1.2 Register the four job handlers in
      `src/butlers/scheduled_jobs.py` under general/home/travel/health.
- [x] 1.3 Add `dispatch_mode="job"` schedules to general/home/travel/health
      `butler.toml` (`*/10`–`*/15` cadence).

## 2. Explicit context MCP tools

- [x] 2.1 `check_context` / `set_context` / `clear_context` tools on the general
      module for explicit dnd/sick per the RFC 0009 writer matrix.

## 3. Spec + verification

- [x] 3.1 Add producer + explicit-tool SHALL-requirements to the `context-bus`
      spec (previously producer-silent).
- [x] 3.2 Unit tests for the deterministic classifiers/helpers.
- [x] 3.3 Integration tests: each producer round-trips `public.user_context`;
      closed-loop check that the notify gate's `get_suppressing_context_signal`
      now sees a live `sleeping` signal (the durable context-deferred branch is
      reachable and preserves the envelope).

## Reconciliation

The originally completed producer work made the context gate reachable while
the then-current notify contract still destructively discarded routine content.
The subsequent `park-owner-default-notifications` change replaces that direct
notify outcome with durable deferral. This change is intentionally not archived
until its delta is synced with the corrected wording below.
