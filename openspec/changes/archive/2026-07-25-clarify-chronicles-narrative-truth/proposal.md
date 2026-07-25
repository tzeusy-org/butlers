## Why

Chronicles can currently present a raw day-close execution trace as owner-facing
prose, can let a cache date disagree with the local day it claims to describe,
and can turn unknown historical coverage into a calm "Quiet day." The archive
must distinguish an honestly covered quiet day from pre-coverage history and
from an owned read or availability failure before later implementation work can
repair those reader-visible failures safely.

## What Changes

- Define a deterministic admission and read-time rejection contract for
  day-close prose: only date-bound, human-facing retrospective text is
  admissible; invalid cache content is never rendered and falls back
  deterministically when the requested day is otherwise covered and available.
- Define the authoritative covered-local-day evidence model and the precedence
  between `no_data`, confirmed covered `quiet`, and
  unavailable/degraded availability. Registry seeding, current feeder state or
  checkpoints, and trailing `daily_rollups` are explicitly not historical
  coverage evidence.
- Bind `earliest_date` and backward archive navigation to the authoritative
  coverage model, including the no-coverage and indeterminate-coverage cases.
- Specify that no-data and unavailable/degraded briefing payloads bypass cached
  LLM prose, including stale prose, and use their own deterministic copy.
- Add an explicit invalid-without-prose response shape to
  `GET /api/chronicler/aggregate/day-close`, separate from both cache miss and
  stale cache state.
- Serialize ownership with blocked `bu-imsks`: this change specifies the
  reader-visible boundary, while that lane retains availability-failure
  classification and its implementation surface. It does not reuse or extend
  the unresolved `chronicler-telemetry-distillation` change.
- Keep this PR OpenSpec-only: no runtime source, migration, database, API
  implementation, frontend, notification, LLM, cache-row, or Beads mutation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `butler-chronicler`: define admissible local-day-bound day-close prose and
  deterministic cache containment.
- `dashboard-chronicles`: define coverage-aware archive states, deterministic
  no-data/unavailable presentation, and truthful backward-navigation bounds.
- `chronicler-api`: define the coverage/availability payload precedence and the
  invalid-without-prose day-close cache response.

## Impact

- Adds only delta artifacts under
  `openspec/changes/clarify-chronicles-narrative-truth/`; canonical specs and
  the active `chronicler-telemetry-distillation` change remain untouched.
- Gives later cache-containment, coverage/no-data, and availability workers a
  shared contract and test matrix without transferring `bu-imsks` ownership.
- Future implementation is expected in Chronicler cache writer/reader,
  editorial payload/API models, and Chronicles rendering only after the
  coordinator dispatches the owning beads.
