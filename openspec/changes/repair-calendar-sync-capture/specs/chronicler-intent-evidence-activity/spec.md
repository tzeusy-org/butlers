## ADDED Requirements

### Requirement: Plan Revisions Are Intent Evidence

The chronicler SHALL project each `change` or `resync` revision in `connectors.calendar_event_revisions` of an instance that starts on or before the owner's local day the revision was observed as one `plan_changed` point event on the `intent` layer, carrying the prior and new start, end and status, keyed on the revision id. A revision with no earlier journaled version SHALL still be projected, with `prior_known` false and null prior fields. A `baseline` revision, and a revision that changes none of start, end and status, SHALL project nothing. A plan revision SHALL NOT assert attendance and SHALL NOT count toward any activity, time or balance aggregate. Only times and status SHALL leave the journal: no title, location, attendee or raw payload reaches the point event, including for a policy-blocked revision.

ID: REQ-chronicler-intent-evidence-activity-002
Source: bu-uhbhzq.1 (run 24 move 1), slice S3
Scope: v1-mandatory

#### Scenario: A moved meeting is a plan change, not attendance

- **WHEN** a `change` revision moves a same-day instance from 14:00 to 16:00
- **THEN** one `plan_changed` intent point event is projected with the prior start 14:00 and the new start 16:00
- **AND** it is excluded from all time and balance totals and asserts no attendance

#### Scenario: Baselines and replays project nothing new

- **WHEN** a `baseline` revision is journaled, or the same revision is projected twice
- **THEN** the baseline projects nothing, and the replay upserts the same point event
