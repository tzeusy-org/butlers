## MODIFIED Requirements

### Requirement: Curriculum request receipt lifecycle

Each accepted curriculum request SHALL settle to a terminal outcome on its receipt row, so that a failure of the detached work is visible to the owner rather than only to the log.

The receipt SHALL carry, in addition to `id`, `topic` and `goal`: `status` (one of `accepted`, `running`, `completed`, `failed`), `session_id`, `mind_map_id`, `calibration_ready_at`, `calibration_notice_outcome`, `calibration_notice_accepted_at`, `failure_reason`, `requested_at`, `triggered_at`, `settled_at`, and `updated_at`.

The detached task SHALL stamp `status = running` and `triggered_at` before handing the request to the butler's `trigger` MCP tool, and SHALL await that tool to completion.

The task SHALL settle `status = completed` only when the triggered session reported success **and** a mind map created at or after `triggered_at` is found. `session_id` SHALL be recorded from the trigger result, `mind_map_id` from that correlation, and `calibration_ready_at` SHALL be set only when the correlated mind map's teaching flow has reached `diagnosing` or a later state.

`calibration_ready_at` attests that calibration **began**. It SHALL NOT be read, rendered, or documented as evidence that the owner was contacted.

Whether the owner was contacted SHALL be recorded separately, from the notification path and never from teaching-flow state. On the completed path the task SHALL consult `public.attention_ledger` for the `source="notify"` dispatch recorded against the session it triggered, and SHALL settle `calibration_notice_outcome` to one of:

- the ledger's own word for the dispatch (`delivered`, `coalesced`, `deferred`, `suppressed`, `failed`);
- `no_record` when the ledger was read and held no notify row for that session;
- `unproven` when the ledger could not be read, or there was no session id to read it with.

`calibration_notice_accepted_at` SHALL be set from the ledger row's own `occurred_at`, and SHALL be set for `outcome = delivered` alone. It means a delivery channel accepted the message; it does not mean the owner read it, and no surface SHALL describe it as though it did.

An absent ledger row SHALL NOT be settled as a failed notice. Ledger recording is best-effort and never raises, so `no_record` records that the evidence is missing, which is a weaker claim than the notice having failed and SHALL stay distinguishable from it.

A settle that has no notice evidence to record SHALL leave both columns as it found them, so a later or duplicate settle cannot blank evidence an earlier one recorded. The two columns SHALL be written as a single unit, because they are decided from one piece of evidence and the database binds them.

The database SHALL enforce that `calibration_notice_outcome` is one of the values above, and that `calibration_notice_accepted_at` is present exactly when the outcome is `delivered` — absent for every other outcome, and absent when the outcome is null.

The task SHALL settle `status = failed` with a stable `failure_reason` on every other exit path: `trigger_unreachable` when the butler could not be reached, `session_error` when the session reported its own failure, and `no_curriculum_created` when a session exited cleanly without producing a curriculum. A clean session exit SHALL NOT by itself settle `completed`.

Settlement SHALL be idempotent: the first terminal write SHALL win, and a later or duplicate settle SHALL be a no-op rather than a contradicting outcome.

A receipt that remains non-terminal for longer than the abandonment timeout SHALL be settled to `failed` with `failure_reason = "timed_out"`, releasing the pending guard. This sweep SHALL run on every submit and every status read, so that an API restart that kills an in-flight task cannot strand the guard.

The database SHALL enforce that a terminal status carries `settled_at`, that a non-terminal status does not, and that `failed` carries a `failure_reason`.

#### Scenario: Successful curriculum settles with evidence

- **WHEN** the triggered session reports success and a mind map was created after `triggered_at`
- **THEN** the receipt SHALL settle `status = completed` with `session_id`, `mind_map_id`, and `settled_at` recorded

#### Scenario: Trigger cannot reach the butler

- **WHEN** the MCP client or `trigger` call raises
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "trigger_unreachable"`

#### Scenario: Session reports its own failure

- **WHEN** the `trigger` result reports `success: false`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "session_error"`
- **AND** the receipt SHALL retain the `session_id` of the failed session

#### Scenario: Session exits without creating a curriculum

- **WHEN** the triggered session reports success but no mind map was created at or after `triggered_at`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "no_curriculum_created"`

#### Scenario: Settlement is idempotent

- **WHEN** a receipt already holds a terminal status
- **AND** a second settle is attempted with a different status
- **THEN** the receipt SHALL retain its original terminal status and evidence

#### Scenario: A failed notice never becomes a delivery claim

- **WHEN** the teaching flow has reached `diagnosing`, so `calibration_ready_at` is set
- **AND** the ledger recorded `outcome = "failed"` for the triggered session's notify
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "failed"` with `calibration_notice_accepted_at` unset
- **AND** `calibration_ready_at` SHALL remain set, because both statements are true and neither implies the other

#### Scenario: A missing ledger row is absence of evidence, not evidence of failure

- **WHEN** the ledger holds no notify row for the triggered session
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "no_record"` with `calibration_notice_accepted_at` unset

#### Scenario: An unreadable ledger is reported as unproven

- **WHEN** the ledger read raises, or the trigger result carried no session id to read it with
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "unproven"` with `calibration_notice_accepted_at` unset
- **AND** the request SHALL still settle its own status normally

#### Scenario: Channel acceptance is recorded with the ledger's own moment

- **WHEN** the ledger recorded `outcome = "delivered"` for the triggered session's notify
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "delivered"` and `calibration_notice_accepted_at` to that ledger row's `occurred_at`

#### Scenario: The database refuses an unbacked acceptance moment

- **WHEN** a receipt row is written with `calibration_notice_accepted_at` set and `calibration_notice_outcome` anything other than `delivered`, null included
- **THEN** the write SHALL be rejected by a CHECK constraint

#### Scenario: Abandoned receipt is swept

- **WHEN** a receipt has been non-terminal for longer than the abandonment timeout
- **AND** a curriculum request is submitted or a receipt status is read
- **THEN** the abandoned receipt SHALL settle `status = failed` with `failure_reason = "timed_out"`
- **AND** a new curriculum request SHALL be accepted
