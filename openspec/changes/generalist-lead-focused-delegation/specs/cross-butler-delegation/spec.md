## REMOVED Requirements

### Requirement: Ledger Of Record

**Reason**: RFC 0038 deletes the cross-butler delegation ledger and its `delegate_*` tools at cutover (D7, Disposition of existing RFCs); a delegated question no longer exists as a unit of work.

**Migration**: A lead calls the owning domain's capability directly through the broker, or delegates a depth-1 child with `goal.delegate` (`REQ-goal-orchestration-001`, `REQ-goal-orchestration-003`). The goal ledger records the delegation tree and child results (`REQ-capability-authorization-012`). The switch runs no destructive migration, so existing `public.delegation_ledger` rows stay in the database without a contract.

### Requirement: Domain Resolution Via Shared Catalog

**Reason**: Resolving "whose domain covers this question" existed only to pick a delegation target; RFC 0038 removes delegation between butlers (D7) and gives the lead a capability catalog instead (D4, D8).

**Migration**: The lead discovers grant-scoped capabilities through RFC 0027 tool search and `guidance.search` (`REQ-capability-authorization-004`); `context.search` fans out over permitted domains' memory and facts (RFC 0038 D8).

### Requirement: Routing Goes Through The Switchboard

**Reason**: RFC 0038 deletes the generic Switchboard `route` tool and route dispatch (D1, D7), the only transport this requirement allows.

**Migration**: Cross-domain capability calls go through the broker with a signed assertion (`REQ-capability-authorization-004`, `REQ-capability-authorization-007`).

### Requirement: Answering Is Asynchronous And Guarded

**Reason**: Waking a target butler's session with a one-shot task is per-butler session spawning, which RFC 0038 removes (D2); `delegate_receive` and `delegate_answer` are deleted (D7).

**Migration**: Focused work runs as a depth-1 child under the same goal, and its result returns to the lead through `goal.await` (`REQ-goal-orchestration-002`, `REQ-goal-orchestration-006`).

### Requirement: Discoverability

**Reason**: The ledger and its API surface are removed with the delegation tools (RFC 0038 D7, Disposition of existing RFCs).

**Migration**: The goal ledger is the record of a goal, its delegation tree and child results (`REQ-capability-authorization-012`). Dashboard delegation rows and attention entries are deferred to the dashboard design change.

### Requirement: Delegated Answer Wake Contract

**Reason**: The answer, callback and wake states exist only for delegated questions, which RFC 0038 removes (D7).

**Migration**: A child's result is returned with `goal.return` and read by its lead from the ledger with `goal.await` (RFC 0038 D8, D12; `REQ-goal-orchestration-006`).

### Requirement: Switchboard-only Delegated Answer Callback

**Reason**: The callback travels over the generic `route` primitive and calls `delegate_wake`, both deleted at cutover (RFC 0038 D7).

**Migration**: None; child results never leave the goal ledger and need no callback (`REQ-goal-orchestration-006`).

### Requirement: Asker-owned Deterministic Return Task

**Reason**: The return task wakes the asking butler's session, which is per-butler session spawning removed by RFC 0038 D2; `delegate_wake` is deleted (D7).

**Migration**: A lead that wants follow-up delegates a new child with the earlier result as context; there is no resume (RFC 0038 D9; `REQ-goal-orchestration-003`).

### Requirement: Delegated Content And Wake Policy Are Fenced

**Reason**: The fenced callback payload and return-task prompt exist only for the removed delegation wake (RFC 0038 D7).

**Migration**: Child results that carry third-party content expose the lead that reads them, and exposure gates consequential effects (`REQ-capability-authorization-006`).

### Requirement: Callback Replay Is Not Re-gated Or Backfilled

**Reason**: Callback retries and legacy wake rows exist only for the removed delegation wake (RFC 0038 D7).

**Migration**: Effects are never replayed blindly (`REQ-capability-authorization-009`); legacy ledger rows stay in the database without a contract, because the switch runs no destructive migration.
