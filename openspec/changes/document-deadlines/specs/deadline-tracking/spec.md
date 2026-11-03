## ADDED Requirements

### Requirement: Template-pinned document deadlines

ID: REQ-deadline-tracking-001
Source: bu-jv1moa.49; RFC 0038 D4, D6 and the dark build; Finance MANIFESTO
Scope: v1-mandatory

Butlers SHALL register a date read from a document through a `paperwork_deadline_set(document_id, kind, target_date, preset, issuer)` core temporal tool, where `kind` is one of `due`, `renewal`, `cancel_by`, `expiry` or `return_by` and `preset` is one of the fixed threshold sets `two_weeks` (14, 3 and 1 days before), `one_month` (30, 7 and 1) or `three_days` (3 and 1). It SHALL create a `task_type='deadline'` task with `dispatch_mode='job'`, no prompt and the fixed job `paperwork_deadline_notify`, so no document text can schedule a session or a goal. One such deadline SHALL exist per `(document_id, kind)`: a repeated call with the same date and preset SHALL leave its status and fired thresholds unchanged, and a new date or preset SHALL supersede it in place, reset its fired thresholds and return its status to `pending` while keeping its `enabled` flag, so document text never re-enables a deadline the owner disabled; a deadline in status `expired`, which the scheduler disabled itself, SHALL be re-enabled. A name already held by any other scheduled task SHALL be refused and left untouched. Thresholds already passed when the deadline is set SHALL collapse to the single most urgent one. A past or same-day date, an unparseable date or one more than 3,660 days ahead, an unknown kind or preset and an empty or oversized document id SHALL each return a typed refusal (`status = "refused"` with a `reason`) and write nothing. The task's `job_args` SHALL carry the taint marker `untrusted_document` (RFC 0038 D6). Because the capability can create no prompt-bearing obligation, it SHALL be catalogued as a `record` effect with idempotency `none` (the natural-key upsert is replay-safe without a broker `effect_id`), no owner authority and no recipient parameters; prompt-bearing deadlines stay `administer`. Until the RFC 0038 cutover it SHALL be in the daemon's tool catalog but disabled on the FastMCP server.

#### Scenario: A bill's due date becomes a reminder-only deadline

- **WHEN** `paperwork_deadline_set(document_id="s3://bucket/general/bill.pdf", kind="due", target_date=<30 days ahead>, issuer="SP Group")` is called
- **THEN** a deadline task with `dispatch_mode='job'`, `job_name='paperwork_deadline_notify'`, no prompt and thresholds 14, 3 and 1 days before is created
- **AND** repeating the call returns the same id with status `unchanged`

#### Scenario: A newer document date supersedes

- **WHEN** the same document and kind are set again with a date two days ahead after the 14-day threshold fired
- **THEN** the same task now targets the new date with thresholds 3 and 1, no fired thresholds and status `pending`
- **AND** if the owner had disabled the task, it stays disabled
- **AND** if the task had expired, it is enabled again

#### Scenario: A prompt-bearing deadline is never rewritten

- **WHEN** the deadline name for a document and kind is already held by a prompt-mode deadline
- **THEN** the call returns `status = "refused"` with reason `name_conflict` and the existing task is unchanged

### Requirement: Job-mode deadline dispatch and fixed-template notice

ID: REQ-deadline-tracking-002
Source: bu-jv1moa.49; RFC 0038 D2, D4 notify exemption and D5
Scope: v1-mandatory

When a `dispatch_mode='job'` deadline crosses a threshold, the scheduler's deadline pass SHALL run its named deterministic job with the task's `job_args` plus a `deadline` context of `target_date`, `days_remaining`, `fired_threshold`, `deadline_status` and `all_thresholds`, SHALL start no session, and SHALL record the threshold as fired exactly as for a prompt-mode deadline. A failed job dispatch SHALL leave the threshold unfired, and a job MAY raise a deferral to leave it unfired without counting as a failure; either way a later tick retries it. The `paperwork_deadline_notify` job SHALL be registered for every butler, bound to that butler as the notice's origin, and SHALL send the owner one Telegram message through the deterministic owner-notify boundary (the owner's own recipient, one attention-ledger row per send) built from a fixed template of a kind label, the issuer, the date and the days remaining. The issuer SHALL be reduced to letters, digits, spaces and `& ' - ,` and at most 60 characters, so no markup, link or URL reaches the owner. A notice that is not delivered SHALL NOT consume its threshold: while quiet hours or a dnd or sleeping context signal suppress owner notices, the job SHALL defer before sending, writing no attention-ledger row, so the threshold fires on the first tick after the suppression ends; a failed send SHALL be recorded once and raised, so the threshold stays unfired and is retried. A suppression that outlasts the target date lets the deadline expire unsent. A row whose kind is unknown SHALL send nothing.

#### Scenario: A due threshold sends a fixed notice

- **WHEN** a document deadline for kind `due`, issuer `SP Group` and target 2026-10-28 fires its 3-day threshold
- **THEN** the job is dispatched with the threshold context and no prompt
- **AND** the owner receives "Paperwork reminder (SP Group): payment due Wed 28 Oct 2026, in 3 days."

#### Scenario: Markup in the issuer stays inert

- **WHEN** the stored issuer is `<b>AXA</b> http://x.io`
- **THEN** the notice names the issuer as `bAXAb httpxio`

#### Scenario: A notice during quiet hours waits

- **WHEN** the final 1-day threshold of a document deadline becomes due while the owner's quiet hours are active
- **THEN** no message is sent, no attention-ledger row is written and `fired_thresholds` is unchanged
- **AND** the first tick after quiet hours end sends the notice and records the threshold as fired
