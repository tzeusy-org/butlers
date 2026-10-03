# Proposed General manifesto and routing amendment

This is proposed text for review, not a change to
`roster/general/MANIFESTO.md`, `openspec/specs/butler-general/spec.md`, or the
Switchboard's current routing configuration. Integrate only after the
capture/vocabulary/ownership-refusal work in `bu-2jtfw.9` has a stable exact
head, then reconcile the final wording and tool inventory with that work.

## General: bounded addition to What You Can Do

> **Remember an identified personal item.** For an object you identify in a
> General collection, we can show where or with whom you last reported it,
> when you reported that observation, and the source of the report. We can
> record your stated move, borrowing, lending, return, or retirement as
> history. We never claim to know its live physical position merely because a
> note, receipt, or old report exists.

## General: addition to What We Refuse to Hold

> General is not a shadow Finance receipt ledger, Relationship person registry,
> or Home maintenance/actuation service. A receipt may be linked as Finance
> evidence but cannot create ownership or prove custody. A person's name is
> unresolved until Relationship identity is confirmed. We do not move,
> install, repair, dispose of, contact anyone about, or automatically return a
> physical object. We do not silently merge two similarly described objects.

The wording must coexist with `bu-2jtfw.9`'s proposed narrowing of "anything
goes" and its collection ownership refusal. It adds a small personal-memory
responsibility rather than a new general capture or inventory platform.

## Switchboard routing proposal

- Route an owner's question about the last *reported* whereabouts or custody
  of an identified personal collection item to General.
- Route transaction, merchant, receipt, refund, and financial-loan questions
  to Finance. Route person identity resolution to Relationship. Route
  connected-device control or automation to Home under its existing gates.
- A phrase such as "the spare adapter" that matches multiple item UUIDs
  requires selection before General records a transition. Routing must not
  collapse those UUIDs, infer ownership from a receipt, or create a collection.
- General may request a bounded Finance fact through Switchboard MCP when the
  owner explicitly links a Finance reference and the receiving tool's read
  authority is reviewed. Failed or unavailable resolution is an evidence
  status, never a custody transition.

No routing configuration is activated by this package.
