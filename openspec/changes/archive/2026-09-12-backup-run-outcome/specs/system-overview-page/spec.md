## ADDED Requirements

### Requirement: Backup Run Outcome

The `/api/system/backups` endpoint SHALL report the outcome of the most recent
backup *run* separately from the age of the most recent backup *artifact*.

These are different questions and one cannot answer the other. The backup script
refuses to publish a bad dump, so a failed run leaves the previous good artifact
in place: after a failure the backup directory is byte-identical to what it was
before, and artifact freshness stays green for up to
`BACKUP_STALE_THRESHOLD_HOURS` more.

Every backup run SHALL therefore record its own outcome where the dashboard
reads it, on a path that survives the failures it reports: the run outcome SHALL
NOT depend on a database connection (the producer is an isolated backup sidecar,
and a database failure is one of the failures it must report), and SHALL NOT be
written only on the success path or only from enumerated failure branches.

A run outcome that is absent, unreadable, or malformed SHALL be reported as
unknown. It SHALL NOT be reported as, or defaulted to, a successful run: an
older deployment and a first-ever run both produce no evidence, and absence of
evidence is not evidence of success.

#### Scenario: Fresh artifact, failed run

- **WHEN** the most recent backup artifact is within the staleness threshold and
  the most recent backup run failed
- **THEN** the response reports `backup_stale: false` and a healthy
  `last_backup_status` — the artifact really is fine —
- **AND** `last_run.result` is `"failed"` with the reason the run failed, so the
  failure is visible on the night it happens rather than 36 hours later as
  staleness of an unrelated file

#### Scenario: Fresh artifact, successful run

- **WHEN** the most recent backup artifact is within the staleness threshold and
  the most recent backup run succeeded
- **THEN** `last_run.result` is `"success"`, distinguishable from the failed
  case by this field alone

#### Scenario: No run outcome recorded

- **WHEN** no run outcome exists for the configured backup directory, or the
  recorded outcome cannot be parsed
- **THEN** `last_run.result` is `"unknown"` with a fixed operator-safe reason
- **AND** no consumer treats it as a successful run

#### Scenario: Run outcome reaches an operator

- **WHEN** the QA infra-state discovery source observes a failed most-recent run
- **THEN** it raises a finding for the failed run itself, rather than waiting
  for the artifact staleness that failure would eventually cause
- **AND** an absent or unparseable run outcome raises no finding, because
  inventing a failure from missing evidence is the mirror image of the bug this
  requirement closes
