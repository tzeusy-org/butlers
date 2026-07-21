## MODIFIED Requirements

### Requirement: Backup State Facts
The `/api/system/backups` endpoint SHALL return the recency and size of the
most recent database backup, a short history of recent backup events, and a
genuinely verified health verdict for the most recent backup and restore drill.
No field in this response SHALL be hardcoded or assumed: every status is
derived from an actual check or explicitly reports that its source is
unavailable.

ID: REQ-system-overview-page-005
Source: Non-Negotiable Rule 1; RFC 0005 § Workflow and Recovery Telemetry; RFC 0007 § Amendment 1: /system Namespace
Scope: v1-mandatory

#### Scenario: Backup endpoint returns recency and structured drill facts
- **WHEN** `GET /api/system/backups` is called
- **THEN** the response body contains `last_backup_at: string | null`,
  `last_backup_size_bytes: number | null`, `backup_source_reachable: boolean`,
  `backup_history: BackupEvent[]`, `last_backup_status: "healthy" | "corrupt" |
  "empty" | "missing"`, and `backup_stale: boolean`
- **AND** each `BackupEvent` is a real artifact verdict (`"healthy"`,
  `"corrupt"`, or `"empty"`) computed from the artifact rather than a
  hardcoded constant
- **AND** `restore_drill` contains `checked_at: string | null`,
  `result: "pass" | "fail" | "pending" | "degraded"`, `detail: string |
  null`, `failure_code: string | null`, `failure_stage: string | null`, and
  `failing_since: string | null`
- **AND** a failed result has a stable failure code/stage and no more than 512
  characters of sanitized detail, while `pass`, `pending`, and `degraded` have
  `failing_since=null`
- **AND** the response wraps in the standard `ApiResponse<BackupFacts>` envelope

#### Scenario: Unavailable backup source degrades gracefully
- **WHEN** the backup metadata source (MinIO/S3 or filesystem) is unreachable
- **THEN** `last_backup_at` and `last_backup_size_bytes` are `null`,
  `backup_source_reachable` is `false`, `backup_history` is empty,
  `last_backup_status` is `"missing"`, and `backup_stale` is `false`
- **AND** the response is HTTP 200 with the degraded payload rather than HTTP 503
- **AND** the frontend renders a backup-status-unavailable indicator rather than
  an error state or a passing backup verdict

#### Scenario: Restore-drill ledger read failure degrades only drill facts
- **WHEN** the restore-drill audit ledger cannot be read because the switchboard
  pool is unavailable or its query fails
- **THEN** `restore_drill.result` is `"degraded"` with a non-null operator-safe
  detail and null failure code, stage, and failure age
- **AND** every other backup fact remains unaffected
- **AND** the response remains HTTP 200 because a ledger-read failure never
  fabricates a successful drill or fails the whole endpoint

#### Scenario: System page shows age only for a current failed drill
- **WHEN** the System page receives `restore_drill.result="fail"` with a
  non-null `failing_since`
- **THEN** the backup tile and system verdict surface a failed restore-drill
  state and a human-readable `Failed since` age adjacent to that current verdict
- **AND** failure code and sanitized detail are secondary diagnostic text with
  semantic status text that does not rely on color alone
- **AND** the page does not claim an owner notification was delivered

#### Scenario: Recovery and unavailable states do not retain historical failure age
- **WHEN** the System page receives a `pass`, `pending`, or `degraded`
  restore-drill result
- **THEN** it renders the corresponding passed, not-yet-run, or unavailable
  state without a stale `Failed since` age
- **AND** a previous failure remains available only through appropriate history,
  not as current dashboard attention

### Requirement: Weekly Restore Drill
The system SHALL attempt to restore the most recent backup artifact into a
scratch database when the persisted restore-drill result is due, verify that
the restore produced real data, tear the scratch database down, and record the
true result. The due policy SHALL be immediate when no result exists, seven
days after a pass, and 24 hours after a failure; it shall never infer cadence
from human-readable error text. This proves a backup is usable rather than
merely present.

ID: REQ-system-overview-page-006
Source: Non-Negotiable Rule 4; RFC 0005 § Workflow and Recovery Telemetry; RFC 0006 § Database Connection Scoping
Scope: v1-mandatory

#### Scenario: Drill succeeds and closes a failure epoch
- **WHEN** a due restore-drill tick finds a backup file and the scratch lifecycle
  completes its cleanup, creation, real-`psql` restore, and non-system-table
  verification stages
- **THEN** it records `result="pass"` in `public.audit_log` with null failure
  code, stage, and failure age
- **AND** `GET /api/system/backups` exposes the passing result
- **AND** a prior contiguous sequence of failures no longer appears as a current
  failure epoch

#### Scenario: Drill failure records structured, sanitized provenance
- **WHEN** a scratch lifecycle stage fails because of client-tool absence,
  scratch cleanup, `CREATEDB` denial, unreadable backup, restore timeout,
  restore error, integrity-check error, unparseable verification output, zero
  restored tables, or an unexpected execution error
- **THEN** it records `result="fail"` with a closed `failure_stage` vocabulary
  of `pre_cleanup`, `create`, `backup_read`, `restore`, `verify`, or
  `post_cleanup`
- **AND** it records a closed `failure_code` vocabulary of
  `client_tool_unavailable`, `scratch_cleanup_failed`,
  `createdb_permission_denied`, `createdb_failed`, `backup_unreadable`,
  `restore_timeout`, `restore_failed`, `integrity_check_failed`,
  `integrity_check_unparseable`, `restore_zero_tables`, or `unexpected_error`
- **AND** the detail is sanitized and bounded to 512 characters without raw
  stderr, credentials, connection strings, dump content, or an unbounded path

#### Scenario: Failed drill creates truthful attention provenance
- **WHEN** a failed restore-drill result has been durably written to
  `public.audit_log`
- **THEN** the job makes a best-effort attention-ledger write with
  `source="restore_drill"`, `outcome="failed"`, no channel or intent, and
  `notification_ref=null`
- **AND** the ledger reason is the stable failure code and its metadata carries
  only the stage, code, bounded sanitized detail, and recorded-at reference
- **AND** a ledger-write failure does not erase or downgrade the audit result
  and does not stop the next 24-hour retry

#### Scenario: No backup file remains a no-result state
- **WHEN** a due restore-drill tick finds no backup file
- **THEN** the tick records neither a pass nor a failure and creates no
  attention-ledger event
- **AND** the absence leaves the no-result state eligible for a later immediate
  attempt when a backup becomes available

#### Scenario: No recorded result is immediately due
- **WHEN** the scheduler has no persisted restore-drill result
- **THEN** it schedules an attempt at the next due check rather than waiting a
  full seven-day interval

#### Scenario: Passed result uses weekly cadence
- **WHEN** the latest persisted restore-drill result is `"pass"`
- **THEN** the next attempt is not due until seven days after that result's
  recorded timestamp

#### Scenario: Failed result retries on recovery cadence
- **WHEN** the latest persisted restore-drill result is `"fail"`
- **THEN** the next attempt is due 24 hours after that result's recorded timestamp
- **AND** each additional failed attempt keeps `failing_since` at the first
  timestamp in the contiguous failed sequence

### Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): the owner must see
  truthful recovery status for their sovereign data.
- Non-Negotiable Rule 4 (`about/heart-and-soul/vision.md`): the deterministic
  recovery loop remains testable and debuggable.
- RFC 0005 § Workflow and Recovery Telemetry: persist structured evidence and
  distinguish execution failure from unavailable observability.
- RFC 0006 § Database Connection Scoping: the scratch lifecycle depends on the
  bootstrap/runtime privilege boundary.
- RFC 0007 § Response Envelope and Amendment 1: /system Namespace: the API and
  System page expose truthful, degraded-safe infrastructure facts.
