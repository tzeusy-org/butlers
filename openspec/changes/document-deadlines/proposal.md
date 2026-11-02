# Document deadlines

## Why

A due, renewal, cancel-by, expiry or return-by date read from a bill or policy can only become a reminder through `deadline_create`, which carries a prompt, starts a session when it fires and is catalogued `administer` (RFC 0038 D4), so it needs per-event owner approval and a goal exposed by document text can never set one (bu-jv1moa.49, slice S1 of lens-documents#3 in the 2026-10-15 Dots superset pursuit). Finance promises never to surprise the owner with a bill.

## What Changes

- New core temporal tool `paperwork_deadline_set(document_id, kind, target_date, preset = "two_weeks", issuer = None)`. It upserts one prompt-free, job-mode deadline per `(document_id, kind)` whose job is the fixed `paperwork_deadline_notify`. The same date and preset again changes nothing; a new date or preset supersedes in place and re-arms the thresholds but keeps an owner's disable (an expired deadline is re-enabled); a name held by any other task is refused. A created or superseded deadline retires the same issuer's deadlines of the same kind from other documents dated on or up to 400 days before it (issuer compared case-insensitively; a document without an issuer retires nothing): they are disabled and marked `superseded_by`, never deleted, and an owner's disable among them carries over; an earlier or far later date keeps both. A write that adds an active deadline to an issuer is refused as `issuer_cap_reached` once it has 10, and a new active row as `total_cap_reached` once the butler has 200, so a tainted goal cannot create unbounded owner reminders. A past, unparseable or more-than-ten-years-ahead date, an unknown kind or preset and an empty or oversized document id each return a typed refusal.
- Three fixed presets (14/3/1, 30/7/1 and 3/1 days before). Thresholds already passed when the deadline is set collapse to the single most urgent one, so a late deadline sends one notice, not a burst.
- The deadline pass runs a job-mode deadline's named job with the threshold context and starts no session. A job that raises (`DeadlineDispatchDeferred` or any failure) leaves the threshold unfired. Prompt-mode deadlines are unchanged.
- `paperwork_deadline_notify` sends the owner a fixed-template Telegram notice (issuer, kind label, date, days remaining) through the existing deterministic owner-notify boundary. A notice that is not delivered leaves its threshold unfired: quiet hours and dnd/sleeping signals defer it to the first tick after they end (no ledger row per attempt), and a failed send raises and is retried. The issuer is reduced to letters, digits, spaces and `& ' - ,`, so no markup, link or URL reaches the owner.
- The tool declares RFC 0038 D4 metadata: effect class `record`, idempotency `none` (the natural-key upsert is replay-safe; there is no broker `effect_id`), no owner authority, no recipient parameters, writes `scheduled_tasks`. The row's `job_args` carries the D6 taint marker `untrusted_document`. It lands dark: cataloged, then disabled on FastMCP, so classify-and-route sessions can neither list nor call it until the goal-runtime cutover.

## Impact

Core temporal tool group, the scheduler deadline pass, the deterministic job registry (every butler gains `paperwork_deadline_notify`), the tool presentation inventory and the daemon's dark-tool set. No migration: job-mode deadlines fit the existing `scheduled_tasks` columns and checks. Rollback: delete the existing document deadlines (`deadline_delete`, names start `paperwork:`) before reverting, because the reverted deadline pass would dispatch a job-mode deadline as a prompt.

Slice S2 (bu-jv1moa.52) adds supersession across documents and caps; it needs no migration and its rollback is the code revert.
