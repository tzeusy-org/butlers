## Why

Dashboard bug-report and dead-letter lanes reserve their terminal side effect before
the relay or write occurs.  A crash after that reservation can leave a turn in
`external_action_in_progress` forever: retries cannot prove whether the visible
effect happened, and the owner can neither be told that it was filed nor that Stop
was confirmed.  That breaks the product's no-fabricated-calm and durable-truth
contracts.

PR #3624 additionally makes a dashboard turn addressable before ingress crosses
Switchboard. A Stop can therefore race a targetless `submitting` ingress; after a
process loss, that intent must not disappear into ordinary loading or trigger a
second delivery. The recovery design must close that state with proof or explicit
ambiguity as well.

## What Changes

- Add a durable, queryable parent action journal and separately recoverable
  effect receipts for dashboard terminal actions (`bug_report` and
  `dead_letter`), including intent, relay identity, owner reply, receipt or
  ambiguity evidence, and reconciliation ownership.
- Give each individual effect an explicit idempotency/receipt strategy. The QA
  staffer will authenticate a dedicated Switchboard-router MCP service
  principal, persist dashboard-action-keyed report receipts, and provide a
  principal-gated lookup contract; dead-letter capture and in-thread replies
  will have their own durable idempotency boundaries. Where proof remains
  unavailable, surface an actionable `ambiguous` terminal state instead of
  retrying blindly or claiming success.
- Add a bounded reconciler that completes, fails, or marks a claimed action
  ambiguous after a crash; a turn must not remain
  `external_action_in_progress` indefinitely.
- Project targetless ingress and unconfirmed Stop intent through the durable
  message read model. A stalled ingress gets an owner-initiated, exact-message
  recovery boundary; a pending Stop gets a bounded no-redelivery reconciliation
  path that reaches a proven outcome or explicit ambiguity.
- Make dashboard conversation APIs and the chat surface render the durable terminal
  result, including an unresolved ambiguity and owner-only manual-resolution
  path, truthfully.
- Expose bounded reconciliation health and sanitized ambiguity evidence so an
  operator can inspect a stuck or ambiguous action without direct database access.
- Add crash-boundary and Stop-during-recovery coverage for both terminal-action
  kinds.
- Replace the repository-owned conversation-scoped cancel endpoint and boolean
  response with the canonical outcome-only message-scoped contract. Migrate the
  dashboard API client, both chat surfaces, tests, and inventory in the same
  implementation change, then delete the aliases before that change archives.
- Amend RFC 0003's recovery guidance so an unproven dashboard route is a
  dashboard-specific ambiguity, not a generic automatic replay candidate.
- Amend RFC 0015 and the QA and Switchboard manifestos in the implementation
  change to record the authenticated durable-inbox exception, permissions,
  recovery ownership, operational modes, and service-level expectations.
- Roll out reconciliation through an owner-controlled observe-safe mode before
  any worker may retry a missing effect.

## Capabilities

### New Capabilities

- `dashboard-terminal-action-recovery`: durable receipts, reconciliation, and
  truthful terminal outcomes for dashboard bug-report and dead-letter actions.

### Modified Capabilities

- `butler-switchboard`: dashboard bug-report and dead-letter lane outcomes become
  durably recoverable rather than an in-process best effort.
- `dashboard-conversations`: conversation lifecycle/read responses expose a
  truthful terminal or ambiguous action outcome, targetless ingress state, and
  bounded Stop/recovery state.
- `dashboard-chat-ui`: the owner sees pending reconciliation, confirmed outcomes,
  and actionable ambiguity without a false filed/cancelled claim or a second
  automatic ingress delivery.
- `staffer-qa`: authenticated Switchboard-originated `report_finding` calls
  produce durable, idempotent receipts and restart-safe discovery work that a
  reconciler and ordinary QA patrol can recover after a crash.

## Impact

- `src/butlers/core/dashboard_turns.py` and the core dashboard-turn migration
  chain
- `src/butlers/core_tools/_switchboard.py`
- `src/butlers/modules/pipeline.py`
- dashboard conversation API models/routes and chat components
- owner-only terminal-action inspection and manual-resolution API endpoints
- authenticated Switchboard-to-QA MCP service-principal wiring, QA
  `report_finding` receipt/discovery/lookup, and dead-letter capture contracts
- removal of the repository-owned conversation-scoped cancel API, boolean
  response model/type, client alias, and their tests after same-change migration
- RFC 0003's dashboard-specific recovery exception
- RFC 0015 plus `roster/qa/MANIFESTO.md` and
  `roster/switchboard/MANIFESTO.md`
- new migration, reconciliation worker ownership, and fault-injection tests

This is the implementation contract for existing P1 Bead `bu-s3qvp` ("Reconcile
ambiguous dashboard terminal external actions"). It is currently an open live
Bead; this planning run neither claims nor mutates it. Before implementation is
dispatched, the owner must approve this changeset and create an explicit HOLD-
gated execution graph rather than treating this text as a release gate. #3624
must first resolve its processing-lease ownership race (a slow conversation
anchor must not permit a displaced worker to invoke), reconcile RFC 0001/RFC
0003/dashboard API inventory vocabulary with dashboard-only no-replay ambiguity,
cover post-acceptance retry and cross-client `SESSION_CANCELLED`, and rebase with
exact-head or merge-result evidence. After #3624 lands at that independently
verified current-base head, this change must
rebase on that exact landing commit and fully reconcile its replacement
`dashboard-chat-ui` → `SSE Client Integration` requirement: preserve or
explicitly owner-approve supersession of every landed Stop clause, including
pre-conversation immutable identity, accessible pending intent, ingress/runtime
fencing, terminal `SESSION_CANCELLED` SSE, and truthful non-calm failure. PR
#3618 must then be explicitly rebased and independently reconciled against this
delta, or closed as superseded only after the owner dispositions each of its
distinct guarantees: the truthful `dispatch_accepted` routed-versus-targetless
receipt and accessible announcement, accountable routed-butler link, and
non-destructive conversation-list/history read recovery. After #3618 no longer
actively modifies the same main requirements, every retained guarantee must be
transplanted into the surviving delta and independently validated; an omitted
guarantee needs an explicit owner rejection. The changes cannot remain competing
active requirements.
This change depends on the durable route/runtime authority in PR #3624 landing
first and on `reconcile-dashboard-conversation-contracts` landing its RFC 0003
vocabulary/provenance amendment before this change applies its separate RFC
recovery amendment. No recovery implementation may silently duplicate or discard
#3618's dashboard receipt/UI changes. It
deliberately does not add a question lane, silently retry an unknown external
action, or duplicate route-inbox cancellation work.
