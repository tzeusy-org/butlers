# butler-health

## ADDED Requirements

### Requirement: Visit brief digest classification

The figure list stored in `health.visit_briefs.digest` (each figure's label, value and fact ids)
SHALL be treated as `confidential` Health content, because it is derived from clinical facts that
Health classifies as `confidential`. The job, its logs, its job result, telemetry, issue and PR text,
and any export SHALL NOT carry digest figure labels, values or fact ids. The calendar event id,
appointment start, brief window, delivery receipt and terminal outcome flags are delivery metadata,
not clinical content.

ID: REQ-butler-health-005
Source: bu-jv1moa.23; [Observed] `src/butlers/jobs/health_visit_brief.py` `compose_brief`
Scope: v1-mandatory

#### Scenario: Job output carries no clinical figures

- **WHEN** `health_visit_brief` runs and stores a digest
- **THEN** its job result and log records SHALL contain only counts, outcome codes and exception class names
- **AND** they SHALL NOT contain any figure label, value or fact id

### Requirement: Bounded visit brief digest retention

Health SHALL prune the clinical content of a visit brief digest once an owner-adopted anchor plus an
owner-adopted delay of `N` ≥ 0 days has passed. The anchor SHALL be one of `appointment` (the row's
`event_start`) or `settlement`. Settlement means `delivered_at` for a delivered brief, or
`updated_at` for a brief that carries a terminal failure flag. A brief that never settled uses
`event_start`. Until the owner adopts an anchor and `N`, pruning SHALL be disabled and no stored
digest SHALL change. Pruning SHALL run inside the `health_visit_brief` run-scoped advisory lock,
before that run selects upcoming appointments. It SHALL work in batches with a per-run cap, so a
backlog drains across successive runs. It SHALL skip any event still held by the in-process
terminal-write fence. Pruning SHALL replace the
digest with a residual receipt that holds `content_pruned_at` and any existing `partial_delivery`,
`telegram_effect_uncertain` and `retryable` flags. It SHALL drop `figures` and the period keys.
Pruning SHALL keep the row, `event_id`, `event_start`, `period_start` and `delivered_at`, and SHALL
NOT delete rows. Pruned content cannot be restored by rollback. Only a backup taken earlier holds
it, until that backup rotates out under its own documented retention. A prune failure SHALL be
reported in the job result and a WARNING log as a reason code without content, and SHALL NOT block
that run's deliveries.

ID: REQ-butler-health-006
Source: bu-jv1moa.23; owner decision bu-yxfwfx pending (proposal options A to D; under D this requirement is removed or rescoped before archive)
Scope: v1-reserved

#### Scenario: Disabled until an anchor and delay are adopted

- **WHEN** no anchor and `N` have been adopted and the job runs over briefs of any age
- **THEN** every stored digest SHALL remain unchanged

#### Scenario: Expired brief keeps its receipt and is never resent

- **WHEN** a delivered brief's adopted anchor plus `N` days has passed
- **THEN** its digest SHALL hold only `content_pruned_at` and any terminal flags
- **AND** `delivered_at` SHALL be unchanged
- **AND** later runs, including one after a reschedule of that event, SHALL send nothing for it

#### Scenario: Terminal outcome survives pruning

- **WHEN** an expired brief carries `partial_delivery` or `telegram_effect_uncertain`
- **THEN** the residual receipt SHALL keep those flags and the event SHALL stay excluded from sends

#### Scenario: Backlog drains across runs

- **WHEN** more rows are eligible than one run's batch cap allows
- **THEN** that run SHALL prune at most the cap
- **AND** later runs SHALL prune the remainder

#### Scenario: Repeated and overlapping runs are safe

- **WHEN** pruning runs again, or two job runs overlap
- **THEN** an already pruned row SHALL NOT change again
- **AND** every due brief SHALL be delivered at most once

#### Scenario: Prune failure is visible and does not block delivery

- **WHEN** the prune statement fails
- **THEN** the job result SHALL report `prune_failed` with the exception class name
- **AND** that run SHALL still send due briefs
