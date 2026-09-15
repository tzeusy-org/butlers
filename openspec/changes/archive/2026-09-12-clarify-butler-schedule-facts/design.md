## Context

`ButlerDetailHeader` currently reduces all enabled, truthy `next_run_at` values
to one minimum timestamp. That makes a stale timestamp appear beside the word
"next" and lets malformed timestamps participate in comparison. The existing
`useSchedules(butlerName)` query already reads schedules every 30 seconds, and
the System section already accepts `?tab=system&section=schedules`.

## Goals / Non-Goals

**Goals:**

- Render only truthful schedule facts from the existing read-only schedule
  query.
- Make an overdue schedule specific, accessible, and actionable through the
  existing schedules section.
- Retain both an overdue fact and a future-next fact when both are true.
- Make selection and relative age deterministic under a fixed wall clock.

**Non-Goals:**

- Recompute, persist, repair, trigger, enable, disable, or otherwise mutate
  schedules.
- Change schedule API schemas, scheduler behavior, status-board activity, or
  add header controls.
- Infer a schedule state from a disabled, null, malformed, or unparsable
  timestamp.

## Decisions

### Derive two facts in the header from validated schedule rows

The header will use a small pure classifier over the existing `Schedule[]` and
a supplied `now` timestamp. It accepts only enabled rows whose `next_run_at`
parses to a finite instant. It selects the oldest overdue instant and the
earliest future instant independently, breaking equal timestamps by schedule
name and id. A single replacement "next" value was rejected because it hides
the future fact when an overdue row exists and cannot name the stale schedule.
A backend-derived status was rejected because the view already has the source
data and the task explicitly excludes scheduler/API changes.

### Treat due-or-past work as overdue and preserve future work separately

An enabled timestamp at or before the current wall clock is an overdue fact;
one strictly after it is a future-next fact. The overdue fact renders before
the future fact so the highest-age signal remains visible, while the future
fact stays visible whenever present. Exact-now is therefore never mislabeled
as a future fire time.

### Use existing time and route primitives

The fact age will use `<Time mode="relative-compact">`, whose output is
deterministic for a fixed clock and carries a semantic `<time>` element. The
header will use the existing `useTickingNow(60_000)` hook so the classification
crosses the future/overdue boundary without waiting for a network response.
The overdue fact will be a native router link to the existing System Schedules
section, with an accessible name that includes the schedule name and age. Its
amber foreground token reinforces the visible "overdue" text rather than
being the sole signal.

## Risks / Trade-offs

- **[Risk]** A schedule can cross its due boundary between schedule polling
  responses. **Mitigation:** recompute from the cached timestamp every minute;
  `useSchedules` continues its 30-second refresh.
- **[Risk]** Multiple schedules can share an instant. **Mitigation:** use name
  and id tie-breakers so which named fact appears is stable.
- **[Risk]** Relative time output can be flaky in tests. **Mitigation:** pin
  fake timers in focused header tests and assert the route and visible labels.

## Migration Plan

Deploy is a frontend-only read-path change. Rolling back restores the prior
header rendering; no data or scheduler state requires migration.

## Open Questions

None.
