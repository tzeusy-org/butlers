## Why

The owner condition ledger (`public.owner_conditions`) already has several producers: Finance
`insight-scan` (`finance:bill-overdue`, `finance:spending-anomaly`), Health `baseline_watch`
(`health:baseline-deviation`, governed by the open change `add-personal-baselines`), Relationship
commitments (`relationship:commitment`, through `core/commitments.py`), and the Switchboard
`reconcile_owner_condition` broker for sessions. The archived change
`generalize-owner-condition-ledger` deferred further producer families at archive: calendar
overcommitment (its task 6.1) and ingestion-time threshold watchers for Home Assistant sensors,
health vitals and spend running totals (its task 6.2). Health vitals are covered by the existing
baseline producer; the calendar, Home Assistant and finance-threshold families have intent in
that task list but no requirement, so none can be dispatched. Today a forward double-booking, a
depleted sensor battery or a budget over its threshold exists only as a re-fired insight
candidate or a dashboard read, which cannot express "still true and still unactioned".

This is a spec-first draft. It turns the gate into one adoption question for the owner; no
implementation is authorized by it.

## What Changes

- ADD three requirements to the `owner-condition-ledger` capability, each modeled on
  "Finance Butler Reconciles Standing Concerns" (in-process, best-effort, complete-snapshot
  reconciliation inside an existing deterministic job, failures never break the host job):
  - Calendar Reconciles Overcommitment Conditions (`calendar:overcommitment`).
  - Home Reconciles Device Threshold Conditions (`home:device-threshold`).
  - Finance Reconciles Budget Threshold Conditions (`finance:budget-threshold`).
  The requirement IDs are REQ-owner-condition-ledger-007, -008 and -010; -009 (Health) was
  dropped and is not reused, so the surviving IDs stay stable.
- Each requirement names its source string, fingerprint identity, config owner, qualifying-input
  rule, resolution rule, degraded-scan behavior and privacy rule, and marks every seam that
  cannot be verified from today's code as `[Unknown]` with a verify task.
- `design.md` records the decisions the `[shaping:needs-spec]` note asked for, each grounded in
  code that exists on main.

## Non-Goals

- No implementation, code, test or migration. Follow-up beads, one per producer, are filed only
  after the owner adopts this change.
- No Health producer: `health:baseline-deviation` already exists (`run_baseline_watch`,
  `src/butlers/jobs/health_baselines.py`) and `add-personal-baselines` governs it; this change
  does not edit that change.
- No `travel_infeasible` conflict kind (waits for `travel_legs`, bu-jv1moa.7).
- No model-backed judgment of any kind; every producer is a deterministic job.
- No change to the ledger schema, the reconcile or resolve tools, or the dashboard conditions
  surface.
- Not RFC 0038 obligation admissions: per RFC 0038 D2 deterministic jobs are unchanged by the goal
  runtime, and these producers stay state-ledger side effects, not admissions.
- No new delivery path. Reconciling a condition sends nothing by itself.

## Impact

- Affected capability: `owner-condition-ledger` (ADDED requirements only; no MODIFIED block, so
  no overwrite risk).
- Affected capability at adoption: `infrastructure-reliability`. REQ-infrastructure-reliability-001
  enumerates the dashboard lifespan's supervised loops, so the new calendar loop requires
  amending that list (the RFC 0007 `supervisors` readiness projection consumes it). No MODIFIED
  block is added by this draft; the amendment lands with the Calendar follow-up (task 1.3).
- Affected roster and code surfaces once adopted: Calendar scan in the dashboard-api process
  (`src/butlers/jobs`, reusing `query_calendar_conflicts`), `src/butlers/jobs/home.py`,
  `roster/finance/jobs/finance_jobs.py`.
- Affected RFCs: none amended.
- Documentation impact: this change only; no doctrine edit.
- Rollback: close the draft PR (draft) or revert it (after adoption, before implementation).
