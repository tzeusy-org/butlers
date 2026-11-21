## Why

RFC 0038 D20 (brokered web research) was adopted with P1 = A (cached for exposed callers) and P2 = A
(third-party goals never research). On 2026-10-20 (`bu-cs33zx`, choice A) the owner said yes to
drafting three refinements as one follow-up amendment whose exact text returns to the owner:

- **Live citation links.** Citation URLs the broker itself recorded may reach the owner as live
  links through a broker-issued handle; every other URL stays inert.
- **Live pinned probes.** An owner-pinned research query run by deterministic code (for example a
  web watch), carrying no goal data, runs live.
- **One approved live search.** From an exposed goal the owner may approve, per event and after
  seeing the exact question, one live search instead of the cached default.

This change proposes the RFC text and the one requirement that would implement it. It authorizes
nothing: no behaviour, credential or spend, and no refinement is in force until the owner adopts the
exact text in RFC 0038 D20 Amendment 1.

## What changes

- Adds `REQ-capability-authorization-025` to `capability-authorization`. It states in its text that
  it takes effect only on adoption and binds nothing until then.
- Does not modify `REQ-capability-authorization-024` or `REQ-capability-authorization-006`.
- `about/heart-and-soul/security.md` gains its paragraph only on adoption; the proposed wording is in
  the RFC amendment.

## Not decided here

`bu-ilqhh0` (pre-switch search mode) and `bu-ob3bbc` question 2 (typed-slot templates). Neither can
override the approved-live-search rule.

Status: **proposed, not adopted or implemented**. Tracking: `bu-6qaju2.6`. Live research stays dark
until the research host (`bu-6qaju2.3`) and broker (`bu-6qaju2.4`) pass their gates.
