## Why

RFC 0038 D20 (brokered web research) was adopted with P1 = A (cached for exposed callers) and P2 = A
(third-party goals never research). On 2026-10-20 (`bu-cs33zx`, choice A) the owner said yes to
drafting three refinements as one follow-up amendment. On 2026-11-22 (`bu-6qaju2.6`) the owner chose
the link rule (C) and the trigger rule (A) and waived the exact-text read and the separate queue
approval for this amendment only, so it adopts by independent conformance review:

- **Live citation links.** Citation URLs the broker itself recorded may reach the owner as live
  links through a broker-issued handle, each keeping its full recorded address (no query or fragment
  trimming) with its destination host shown beside it; every other URL stays inert.
- **Live pinned probes.** An owner-pinned research query run by deterministic code (for example a
  web watch), carrying no goal data, runs live and shares the D20 caps with no new spend.
- **One approved live search.** From an exposed goal the owner may approve, per event and after
  seeing the exact question, one single-use live search instead of the cached default. A decline,
  expiry (the existing 48-hour goal-approval lifetime) or Stop runs nothing, with no cached fallback.

## What changes

- Adds `REQ-capability-authorization-025` to `capability-authorization`.
- Does not modify `REQ-capability-authorization-024` or `REQ-capability-authorization-006`.
- `about/heart-and-soul/security.md` gains one paragraph describing the three refinements.
- The change stays under `openspec/changes/`, like other adopted but unbuilt requirements.

## Not decided here

`bu-ilqhh0` (pre-switch search mode) and `bu-ob3bbc` question 2 (typed-slot templates). Neither can
override the approved-live-search rule.

Tracking: `bu-6qaju2.6`. Adoption authorizes no implementation, credential or spend; live research
stays dark until the research host (`bu-6qaju2.3`) and broker (`bu-6qaju2.4`) pass their gates.
