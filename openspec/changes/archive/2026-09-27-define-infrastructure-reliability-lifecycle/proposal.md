## Why

Infrastructure failures currently rely on one-shot audit markers and on
observations that can be incomplete. That lets a known outage become silently
"already escalated," lets a failed scan impersonate recovery, and can turn the
same active infrastructure condition into repeated QA execution work.

## What Changes

- Define a durable infrastructure-reliability capability with stable producer
  identity, append-per-episode condition history, complete-snapshot recovery
  authority, and a bounded re-escalation schedule.
- Replace deployment drift's one-shot "escalates exactly once" behavior with
  lifecycle-driven L0/L1/L2/L3 escalation and seven-day recurring escalation
  while a condition remains active. **BREAKING**: a continuing condition is no
  longer permanently silenced after its first escalation. The
  `deployment-and-drift` / `QA Escalation After Sustained Drift` spec delta for
  this lives in the `retire-drift-composition-escalation-model-step-1-retire` /
  `-step-2-restore` pair, not here: the two models contradict each other on two
  scenarios and a `## MODIFIED` block cannot retire a baseline scenario name.
  Tasks 2.1 and 2.2 below remain this change's implementation work.
- Require active `infra_state` conditions to be suppressed as a decision-only
  QA admission result before `create_or_join_attempt`, without creating an
  attempt, session, worktree, or LLM invocation.
- Define named supervision and shutdown semantics for the dashboard's
  expected-infinite lifespan loops.
- Clarify that connector liveness is derived from heartbeat recency while the
  stored connector state remains separate operational-health evidence.

## Capabilities

### New Capabilities

- `infrastructure-reliability`: Durable condition identity, episode lifecycle,
  snapshot reconciliation, bounded re-escalation, QA admission suppression,
  dashboard-loop supervision, and liveness authority.

### Modified Capabilities

- `deployment-and-drift`: Replace first-detected/already-escalated debounce
  semantics with lifecycle reconciliation and bounded re-escalation.
- `healing-session-tracking`: Record an active infrastructure condition as a
  decision-only dispatch event with no attempt linkage.
- `qa-investigation-dispatch`: Order active-`infra_state` suppression after
  normal eligibility and before the atomic attempt claim.
- `qa-triage`: Preserve visible, source-agnostic findings when dispatch marks
  them as suppressed by an active infrastructure condition.
- `connector-base-spec`: Make heartbeat-derived liveness authoritative and
  keep the stored health state semantically independent.

## Impact

Later implementation will add a deterministic shared condition ledger and
lifecycle service, adapt deployment drift and calendar deadman producers,
reconcile `InfraStateSource` findings, update QA admission, supervise the
dashboard lifespan loops, and audit connector read models. This planning-only
change does not alter code, migrations, configuration, runtime behavior, APIs,
frontend surfaces, connector writers/startup, generic producer heartbeats,
scheduler defaults, or external-monitor provisioning. It does not modify the
active core-daemon/delegation, connector writer/startup, or direct-audit-result
work.
