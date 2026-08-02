## MODIFIED Requirements

### Requirement: Red Clause on the /system Page

The `/system` dashboard page SHALL surface migration drift as a distinct,
visually red clause when drifted, and SHALL fold it into the page's overall
verdict banner. The banner SHALL also apply the same source-honesty settlement
rule to the System Overview's database and data-egress sources.

#### Scenario: Drift renders as a red tile

- **WHEN** `is_drifted` is `true`
- **THEN** the `/system` page's drift tile renders a red badge naming the
  count of drifted chains, plus one line per drifted `(schema, chain,
  expected_head, actual_revision)` triple

#### Scenario: Verdict banner surfaces drift as a problem, not silently

- **WHEN** the page's computed verdict banner (`SystemVerdictBanner`) is
  rendered and drift is present
- **THEN** the banner's problem list includes a line naming the drifted
  chain count, annotated with "escalated to QA" once `escalated` is `true`
- **AND** the banner never renders its "all clear" state while the drift
  source is still loading or has failed to load

#### Scenario: Verdict source settlement includes database and egress

- **WHEN** the computed verdict receives database or egress query state
- **THEN** it remains unsettled while either source is loading
- **AND** it names a settled database failure or non-403 egress failure as an
  unavailable source instead of rendering all clear
- **AND** an egress `isForbidden` HTTP 403 remains settled owner-only limited
  visibility, not a failure
