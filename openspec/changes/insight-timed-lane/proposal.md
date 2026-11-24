# Timed insight lane and shared delivery claim

## Why

Some insights are only worth saying at a moment: "you left; the back door is open" means nothing an hour later in a digest. The broker had no lane for a candidate that must be sent inside a window or not at all (bu-jv1moa.8). Separately, the digest and urgent cycles selected on `status = 'pending'` and marked `delivered` with no status guard, so two overlapping cycles could both send one candidate.

## What Changes

- `public.insight_candidates` gains `lane` (`digest` default, `timed`), `deliver_after`, and the claim columns `claim_id`, `claimed_at` and `attempt_started_at`, plus a unique timed `dedup_key` (core_334).
- Every delivery worker claims its candidates with one compare-and-set, all or none, under one advisory lock with its budget count, and writes the final disposition only while holding the claim. Digest retry semantics are unchanged; a stale digest claim is released.
- A dark timed sub-cycle (`insight_timed_subcycle`, `TIMED_INSIGHTS_LIVE = False`, unscheduled) sends due timed candidates standalone under RFC 0011 Amendment 8's window and hold rules. A timed send spends a configured-budget slot and the lane sends at most six a day. An ambiguous timed send is never resent.
- `propose_insight_candidate` takes `lane` and `deliver_after`, and refuses timed proposals while the lane is dark.

## Impact

Switchboard insight broker, the core migration chain, and the scheduled-job registry. No new role and no producer in PR-A.

PR-B adds the first producer, the Home departure card: `home.departure_watch` (what to check, owner labels, secure states) and `home.departure_cards` (per-edge evidence) in home_006; the dark, unscheduled `home_departure_watch` job; the `("home", "departure_still_open")` premise probe; and `public.resolve_home_departure` (core_335), a SECURITY DEFINER that answers open/closed/unknown to `butler_switchboard_rw` only. Departure fixes, arrival cards and an immediate lane stay with bu-jv1moa.29.

Activation flips both flags, logs in the perception reader and adds both schedules in one owner-approved change.
