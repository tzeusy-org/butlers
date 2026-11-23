## Why

Owner decision bu-nt2eda (Q1 = A, Q2 = A, Q3 = cap 50 / 90 days / digest) put owner-authored,
notify-only watches and card-approved correlation rules in scope, but RFC 0019 still reads
"Parked", RFC 0038 S4 still defines only the first-switch AST, and the RFC 0019 carrier text
added by bu-dhloi1.19 is labelled "adoption pending". M7 task bu-4c7nvp.1.5 cannot build
watches, typed read checks, mode and routine guards, Finance alert migration or rule graduation
until that contract is adopted and names the extended predicate language.

The recorded owner boundary says no amendment text is merged without exact-text approval, so
this change is an owner-merged, text-only PR. Nothing here authorizes a runtime, a deploy, a
switch, or any live data change.

## What Changes

- RFC 0019: adopt the notify-only narrowing (promote the carrier section from "adoption pending"),
  update the status line, and correct the stale vision citation in "Why these items are parked".
- RFC 0038 S4: add the M7 extension of the one closed AST (`expectation`, `read_check`,
  `condition`, `mode`, `routine_deviation`, `bundle`), the watch lifecycle, the Finance migration
  rule, the guard/mode rule and the rule-graduation rule. The first-switch AST is unchanged.
- ADD the `owner-watches` capability with REQ-owner-watches-001 through -008.
- Keep the rejected calendar auto-response, the general trigger-to-action engine, third-party
  delivery and every other egress effect parked.

## Non-Goals

- No code, migration, table, tool, scheduler job or dashboard surface.
- No new connector or source. Observed-sleep fusion uses existing phone, Home Assistant and
  wearable inputs only and reports unknown when they are missing.
- No Chronicler routine storage or `expect.*` publication (bu-xxfkhk owns it) and no
  routine-anchored obligation proposal path.
- No change to RFC 0011 attention budgets, urgency or step-up policy.

## Impact

- `about/legends-and-lore/rfcs/0019-proactive-egress-and-automation-parked.md`
- `about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md` (S4)
- `about/legends-and-lore/README.md` (RFC 0019 index row)
- New capability `owner-watches`. It sits beside the pending `condition-evaluation` delta in
  `generalist-lead-focused-delegation`, which is not yet archived, so it cites those IDs instead
  of modifying them.
