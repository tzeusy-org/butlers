## ADDED Requirements

### Requirement: Internal maintenance rollup in Dashboard Now

Dashboard Now SHALL remain owner-focused by default and SHALL not use
maintenance runs whose `data.success` is exactly `true` as ordinary recent
activity. A `false` value is a failure, while `null`, missing, and nonboolean
values are running or unknown and SHALL remain ordinary activity while the
lens is disabled. It SHALL provide the same accessible, URL-backed Internal
lens as the Timeline. When enabled, Dashboard Now SHALL render compact
per-butler maintenance rollups from the Timeline event machine class and link
them to the Timeline with its Internal lens enabled. Failed maintenance
sessions SHALL remain visible as error activity while the lens is disabled.
Dashboard Now SHALL render a confirmed failure with a textual `failed` marker
and destructive error treatment rather than leaving its status only in
non-rendered row detail. An Internal rollup with failed runs SHALL visibly
include its exact failed-run count.

#### Scenario: Dashboard Now defaults to owner activity

- **WHEN** Dashboard Now receives successful maintenance Timeline events and
  the URL does not include `internal=1`
- **THEN** it does not render those events as ordinary activity rows
- **AND** it continues to render owner activity and error rows

#### Scenario: Dashboard Now keeps running and unknown maintenance visible

- **WHEN** Dashboard Now receives a maintenance Timeline event whose
  `data.success` is `null`, absent, or nonboolean and the URL does not include
  `internal=1`
- **THEN** it renders that event as ordinary recent activity

#### Scenario: Dashboard Now renders failed maintenance as a visible failure

- **WHEN** Dashboard Now receives a maintenance Timeline event whose
  `data.success` is exactly `false` and the URL does not include `internal=1`
- **THEN** it renders the event with a textual `failed` marker and destructive
  error treatment, rather than only an ordinary `activity` marker
- **AND** it continues to treat `null`, missing, and nonboolean success values
  as visible running or unknown activity rather than failures

#### Scenario: Internal Dashboard Now lens groups maintenance by butler

- **WHEN** the operator enables the Internal lens and multiple loaded
  maintenance events belong to the same butler
- **THEN** Dashboard Now renders one maintenance rollup with the exact loaded
  event count for that butler
- **AND** when the rollup contains failed runs, it visibly includes the exact
  failed-run count and failure treatment
- **AND** its link opens the Timeline with `internal=1`
