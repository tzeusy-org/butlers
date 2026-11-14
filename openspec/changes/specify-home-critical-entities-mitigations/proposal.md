## Why

The safety sentinel (bu-jv1moa.6) alerts on leak, smoke, CO and gas and never calls Home Assistant.
Two physical-safety promises from the same outcome were left out of it and have no specification:

- **Critical entities.** The owner can mark an entity (a water main valve, a gas valve, a garage
  door, a space heater plug) as one that never moves without the owner's own approval, even when
  RFC 0028 would run the call unprompted as `reversible`.
- **Approved safety mitigations.** When a safety condition opens, the owner is offered the one exact
  call they declared for it (close this valve), per item, with the pre-call state captured and a
  post-condition receipt. It is not a bundle, and it never runs by itself.

Both change what RFC 0028 lets run without approval, so they need an exact RFC amendment that the
owner adopts. This change proposes that text and the requirements that would implement it. It
authorizes nothing: no entity is marked critical, no call is reclassified and no approval is
loosened until the owner adopts the amendment.

## What changes

- Propose **RFC 0028 Amendment 1** (exact text in design.md, marked owner adoption required):
  critical entities, the fail-closed registry, pre-call state, the `already_satisfied` outcome and
  approved safety mitigations.
- Specify an **owner-only critical-entity registry**: written only through owner-authenticated
  dashboard routes, never derived from a label, area, attribute, name or model.
- Specify that **a call naming a critical entity always parks**, that no standing rule, decision
  memory or capability grant approves it, and that **a lost or unreadable registry fails closed**
  (every call is treated as naming a critical entity).
- Specify **pre-call state and target binding** for approved calls on critical entities and for
  mitigations: a live read of exactly the stored target, fresh within a fixed bound, kept on the
  receipt; stale, missing or unreadable state sends nothing.
- Specify the **`already_satisfied` no-op**: a pre-call state that already meets the post-condition
  sends nothing and never reads as success.
- Specify **per-item safety mitigations**: owner-declared exact calls per safety-watch sensor,
  parked when the `home:safety` condition opens, replayed only while that condition is open and the
  declaration still digests to the stored call, withdrawn when it resolves.
- Specify **one physical executor**: departure fixes and mitigations dispatch only through the Home
  module's `ha_call_service` under the approvals executor, with one replay gate and one status
  resolver (the S-A/S-B substrate from `specify-home-departure-fixes-arrival`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-home-assistant`: adds REQ-module-home-assistant-001 through 004 (critical registry,
  critical parking, pre-call state and target binding, `already_satisfied`).
- `home-deterministic-jobs`: adds REQ-home-deterministic-jobs-013 through 015 (mitigations, their
  replay premise and cancellation, the single executor). IDs 008 to 012 belong to
  `specify-home-departure-fixes-arrival`. The resolver's closed set in REQ-home-deterministic-jobs-011
  is not modified here, because that change is not yet archived and two retained definitions of one
  ID are ambiguous; design.md records the follow-up delta.

## Impact

Future owners:

- Home migrations (`critical_entities`, `safety_mitigations`, the receipt's `pre_state` column and
  `already_satisfied` status), the Home module's `ha_call_service` gate, the shared replay gate,
  owner-only Home API routes and Devices panels, the status resolver and `core_337`'s definer
  function.

Unaffected:

- The safety sentinel's read-only alert: its priority, text, timing and delivery path do not change,
  and a mitigation failure never delays or blocks it.
- RFC 0028's risk map, unknown-is-protected rule and receipt fencing; the approvals state machine (no
  new approval status).
- Every call that names no critical entity, while the registry is readable.

Status: **specification proposed, not adopted or implemented**. The RFC 0028 amendment requires
owner adoption of its exact text. Tracking: `bu-jv1moa.33`. Base: `origin/main` on 2026-11-15. No
registry entry, mitigation, flag, button or schedule is activated by this change.
