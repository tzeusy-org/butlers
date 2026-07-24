## 1. bu-27dxl.5.2 - Durable protocol representation and propagation

- [x] 1.1 Add the delegation-ledger representation, targeted grants, and
  repository operations required by cross-butler-delegation: Delegated Answer
  Wake Contract; preserve answered as durable answer truth while recording wake
  key, state, callback attempts/results, and task binding.
- [x] 1.2 Update delegate_answer and the Switchboard route adapter to implement
  cross-butler-delegation: Switchboard-only Delegated Answer Callback, including
  authoritative ledger identity checks and callback failure/retry truthfulness.
- [x] 1.3 Implement the trusted asker-side delegate_wake endpoint and
  deterministic local task reconciliation required by cross-butler-delegation:
  Asker-owned Deterministic Return Task; do not introduce direct sibling-schema
  task writes or scheduler-core changes.
- [x] 1.4 Add migrated-Postgres and focused route/tool tests for first-answer
  authority, wrong actor/row, changed answer, duplicate/reconnect, callback
  failure, crash-after-insert reconciliation, task conflict, untrusted payload,
  late answer, legacy row, DND/floor exclusion, and no-regating.
- [x] 1.5 Verify the implementation's MCP inventory against core-daemon:
  Delegation Core Tool Inventory And Admission Boundary, including non-staffer
  admission and the Switchboard-only delegate_wake path.

## 2. bu-27dxl.5.3 - Delegation activation and guidance

- [x] 2.1 Add the separately scoped runtime-config validation and configuration
  activation for the delegation group; preserve existing groups and do not
  alter ledger/callback semantics.
- [x] 2.2 Activate and verify the intended non-staffer MCP inventory for
  Finance and Relationship, including restart/release evidence and staffer
  exclusion, as required by core-daemon: Delegation Core Tool Inventory And
  Admission Boundary.
- [x] 2.3 Add the shared prompt/guidance and local roster reachability checks
  without changing a bare CLAUDE include contract, a schedule, a briefing
  producer, or any user delivery behavior.

## 3. bu-27dxl.5.4 - Bounded briefing producer

- [x] 3.1 Implement one deterministic Relationship-to-Finance delegation seed
  through the normal delegate_ask/Switchboard route with a deterministic
  origin/dedup key and no direct Finance schema/API access.
- [x] 3.2 Prove that qualifying/no-birthday/duplicate/route-failure paths leave
  the primary briefing contribution correct and create no owner-facing egress.
- [x] 3.3 Verify cross-butler-briefing-contribution: Delegated Return Wakes Are
  Not Briefing Contributions: no same-day composer/envelope join, no
  briefing/daily or combined-briefing mutation from the return loop, and no
  RFC 0010 reuse.

## 4. Exact-boundary verification

- [x] 4.1 Run the focused lint, format, migrated-DB, route, core-tool, and
  briefing tests named by the implementation children; record exact-head
  evidence for every scenario in the three delta specs.
- [x] 4.2 Run scoped strict OpenSpec validation and a diff review confirming no
  runtime source, migration, data mutation, schedule/config patch, direct
  sibling-schema access, user notification, catalog/QA/subscription work, or
  PR #3513 owner-Telegram wake-recovery behavior leaked into the wrong child.
- [x] 4.3 Complete bu-27dxl.5.5 reconciliation only after .5.2, .5.3, and .5.4
  each provide reviewed exact-head evidence against their mapped requirement
  sections.
