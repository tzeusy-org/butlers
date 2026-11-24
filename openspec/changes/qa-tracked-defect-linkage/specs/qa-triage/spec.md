## ADDED Requirements

### Requirement: Tracked Findings Are Not Re-Investigated

QA triage SHALL hold back a finding whose fingerprint has an unexpired link in state `tracked` to a
bead, pull request or issue, by persisting it with `dedup_reason = 'tracked'` and dispatching no
investigation. The gate SHALL rank after dismissal and before cooldown, and a
tracked finding SHALL cost no investigation session and no circuit-breaker budget. A link in state
`fix_merged` SHALL NOT hold a recurrence back, because the fix is not known live: the finding SHALL
be dispatched as novel with `structured_evidence.fix_merged_link` and `tracker_ref` attached, and
the link SHALL stay `fix_merged`. A link in state `awaiting_recovery` SHALL NOT hold a recurrence
back either: the finding SHALL be admitted as novel with
`structured_evidence.regression_of` naming the link, and the link SHALL move to `regressed`. A link
past its `expires_at`, or in state `regressed` or `closed`, SHALL NOT gate. A failure to read the
link store SHALL fail open: the finding SHALL dispatch as if no link existed, with one warning and
one counter increment, and the patrol SHALL NOT fail. A link SHALL be created
only by the owner or, for QA's own pull requests, by QA; a model-inferred or text-matched link SHALL
NOT be created.

ID: REQ-qa-triage-007
Source: bu-ejow5p.9 (run 25 move 9), slice S1; owner decisions on bu-ejow5p.9 (owner-only links, bead refs as unverified assertions)
Scope: v1-mandatory

#### Scenario: A linked finding is tracked, not dispatched

- **WHEN** a patrol finds a fingerprint with a `tracked` link
- **THEN** the finding is persisted with `dedup_reason` `tracked` and no investigation is dispatched

#### Scenario: A dismissal outranks a link and a link outranks the cooldown

- **WHEN** a fingerprint has both a dismissal and a link
- **THEN** the finding is `dismissed`
- **AND** with a link and a recent terminal attempt the finding is `tracked`

#### Scenario: A merged but undeployed fix never hides a recurrence

- **WHEN** a finding recurs under a `fix_merged` link
- **THEN** it is dispatched as novel carrying the link, and the link stays `fix_merged`

#### Scenario: An unreadable link store does not suppress

- **WHEN** reading the link raises
- **THEN** the finding is dispatched as novel and a warning and counter record the failure

#### Scenario: A recurrence after the fix is a regression

- **WHEN** a finding recurs under an `awaiting_recovery` link
- **THEN** it is admitted as novel carrying `regression_of` the link, and the link becomes `regressed`

#### Scenario: An expired or closed link does not gate

- **WHEN** a link is past `expires_at` or closed
- **THEN** the finding is triaged as if no link existed
