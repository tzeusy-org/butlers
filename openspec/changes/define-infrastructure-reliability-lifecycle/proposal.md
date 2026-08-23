> [!CAUTION]
> **DO NOT ARCHIVE THIS CHANGE UNTIL bu-ka9vx LANDS.**
>
> `deployment-and-drift` / `QA Escalation After Sustained Drift` currently carries **10
> scenarios: 6 stating the episode model this change introduces, and 4 still written in the
> composition-fingerprint model it retires.** Two of those 4 contradict their replacements
> outright:
>
> - `First sighting does not escalate` mandates "a first-detected marker is persisted (keyed
>   by a stable fingerprint of that composition)", while `First sighting opens L0 evidence`
>   states it "does not use a composition-wide audit marker as current-state authority".
> - `Drift past the 24h threshold escalates exactly once` persists an escalated marker "so
>   subsequent ticks do not re-escalate it", while `Continuing drift re-escalates without
>   additional healing attempts` requires exactly that re-escalation.
>
> The 4 are not stale leftovers and must not simply be deleted here: they are baseline
> scenarios, and a `## MODIFIED` block may not drop a scenario name the baseline still
> carries. Removing them to tidy the requirement silently deletes them from the baseline on
> archive.
>
> Every available signal is green on this: `openspec archive` succeeds, `openspec validate
> --strict` passes, and the archived-requirements guard sees the headers land. Nothing will
> stop you. Archiving anyway writes the contradiction straight into `openspec/specs/`, where
> it becomes the baseline every later change is validated against.
>
> **bu-ka9vx** resolves it with the two-change retire/restore procedure -- one change removing
> the requirement, a second re-adding it with the 6 episode-model scenarios only. Archive that
> first, then this.

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
  longer permanently silenced after its first escalation.
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
