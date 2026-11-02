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

### Requirement: Document deadline supersession across documents and caps

ID: REQ-deadline-tracking-003
Source: bu-jv1moa.52; RFC 0038 D6; Finance MANIFESTO
Scope: v1-mandatory

When `paperwork_deadline_set` creates a document deadline or supersedes one in place, it SHALL retire every other document's live job-mode document deadline with the same `kind`, the same issuer (sanitized text compared case-insensitively) and a target date on or before the new one and at most 400 days before it, and SHALL return their ids as `replaced`. A retired deadline SHALL NOT be deleted: it SHALL be disabled and keep its row, with `job_args` recording `superseded_by` (the replacing task's id) and its prior `enabled` flag, so it never fires, stays visible to the owner and can be restored. A superseded deadline SHALL NOT count toward the caps, SHALL NOT be retired again and SHALL carry no disable; re-dating a superseded document SHALL restore its prior `enabled` flag only while no write has touched the row since its supersession (`job_args.superseded_at` still equals `updated_at`); after an owner's enable or disable it SHALL keep its current flag, so document text never re-enables an owner's disable. A deadline without an issuer SHALL neither replace nor be replaced by another document's. A deadline with a later target date, or one more than 400 days before the new date, SHALL be kept, so neither a newer nor a forged far-future document drops a reminder still ahead of it. If any retired deadline was disabled by the owner, that is, disabled while its status was neither `expired` nor `completed`, the new or superseded deadline SHALL be disabled too, so supersession never re-enables a reminder the owner turned off; an `expired` or `completed` predecessor carries no disable. A write SHALL be refused with reason `issuer_cap_reached` when it would add an active deadline to an issuer that already holds 10 (a new row, a re-dated expired, completed or superseded row, or an existing row whose issuer changes), counting live deadlines whose status is not `expired` or `completed`, after retirement, with documents without an issuer sharing one bucket; a write that adds an active row SHALL be refused with reason `total_cap_reached` when the butler already holds 200. Every refusal SHALL write nothing, and concurrent calls in one butler schema SHALL be serialized so the caps hold.

#### Scenario: A renewed notice retires the older one and keeps the owner's disable

- **WHEN** the owner disabled the `due` deadline from AXA's first bill and AXA's next bill sets a later `due` date
- **THEN** the first bill's deadline is disabled, marked `superseded_by` the new one and listed in `replaced`
- **AND** the new deadline is created disabled

#### Scenario: An earlier or far later date keeps both reminders

- **WHEN** a second AXA document sets a `due` date before the existing AXA `due` deadline, or more than 400 days after it
- **THEN** both deadlines stay live and `replaced` is empty

#### Scenario: The issuer cap refuses

- **WHEN** an issuer already holds 10 active document deadlines and an eleventh document from it is set with an earlier date, or another document's deadline is re-set naming that issuer
- **THEN** the call returns `status = "refused"` with reason `issuer_cap_reached` and nothing is written

### Requirement: Job-mode deadline dispatch and fixed-template notice

ID: REQ-deadline-tracking-002
Source: bu-jv1moa.49; RFC 0038 D2, D4 notify exemption and D5
Scope: v1-mandatory

When a `dispatch_mode='job'` deadline crosses a threshold, the scheduler's deadline pass SHALL run its named deterministic job with the task's `job_args` plus a `deadline` context of `target_date`, `days_remaining`, `fired_threshold`, `deadline_status` and `all_thresholds`, SHALL start no session, and SHALL record the threshold as fired exactly as for a prompt-mode deadline. A failed job dispatch SHALL leave the threshold unfired, and a job MAY raise a deferral to leave it unfired without counting as a failure; either way a later tick retries it. The `paperwork_deadline_notify` job SHALL be registered for every butler, bound to that butler as the notice's origin, and SHALL send the owner one Telegram message through the deterministic owner-notify boundary (the owner's own recipient, one attention-ledger row per send) built from a fixed template of a kind label, the issuer, the date and the days remaining. The issuer SHALL be reduced to letters, digits, spaces and `& ' - ,` and at most 60 characters, so no markup, link or URL reaches the owner. A notice that is not delivered SHALL NOT consume its threshold: while quiet hours or a dnd or sleeping context signal suppress owner notices, the job SHALL defer before sending, writing no attention-ledger row, so the threshold fires on the first tick after the suppression ends; a failed send SHALL be recorded and raised, so the threshold stays unfired and is retried. Failed attempts SHALL back off: every attempt's attention-ledger row SHALL carry one `dedup_key` per origin butler, deadline document, kind, target date and threshold, and after `n` failed rows under that key the job SHALL defer, writing no row, until 5 minutes doubled per further failure and capped at one hour have passed since the newest of them; the threshold SHALL still fire on the first attempt that delivers. The backoff SHALL derive only from those ledger rows and SHALL fail open when the ledger cannot be read. A suppression that outlasts the target date lets the deadline expire unsent. A row whose kind is unknown SHALL send nothing.

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

#### Scenario: A Switchboard outage backs off instead of flooding the ledger

- **WHEN** the 3-day threshold's send fails because the Switchboard is unreachable and the scheduler ticks every minute
- **THEN** one `failed` attention-ledger row is written and the next ticks within 5 minutes defer with no row, the following wait being 10 minutes, then 20, up to an hour between attempts
- **AND** `fired_thresholds` is unchanged until an attempt delivers, which records the threshold as fired
