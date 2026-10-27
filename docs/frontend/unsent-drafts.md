# Browser-local unsent drafts

The dashboard keeps the approved unsent fields in this browser profile. This is
not a server backup, encryption boundary, or promise that an uncommitted edit
survives process termination. People using the same profile and scripts running
on this origin can read the records. Each enrolled form exposes this limitation
under **Browser draft privacy**.

The adopted contract is
[`persist-safe-dashboard-unsent-drafts`](../../openspec/changes/persist-safe-dashboard-unsent-drafts/design.md).
The current implementation covers its store, eligibility, form, and local chat
restoration portions. The backend exact-message resolver and trusted durable
turn projection are absent. Chat therefore does not persist attempts, recover
outcomes, search message text, or resend automatically. Its existing successful
POST response can clear that request's submitted content revision; an unknown
request leaves its locally committed text recoverable. The existing explicit
Retry keeps its in-memory message-ID binding to the submitted revision, so actual
acceptance clears that revision while preserving newer unsent input. Existing `message_id`,
retry, page-context capture, and Stop behavior remain authoritative.

## Enrolled bundles

Only explicit `useDraft` registrations in the owning components enroll fields.
Shared inputs, Dialog, and Sheet do not become draft surfaces. The structural
`npm run lint:draft-eligibility` gate checks the exact owners and fields and
rejects aliases, unknown surfaces, credential-purpose controls, and registrations
in provider configuration, `/secrets`, or shared controls.

| Surface | Stored fields | Structural context |
| --- | --- | --- |
| `chat.message` | message | butler, conversation ID or new |
| `health.condition` | name, status, diagnosedAt, notes | create/edit, condition ID or new |
| `health.meal` | type, description, eatenAt, calories, protein, carbs, fat, notes | create/edit, meal ID or new |
| `health.measurement` | type, scalar, systolic, diastolic, valueObject, measuredAt, notes | create/edit, measurement ID or new |
| `health.medication` | name, dosage, frequency, schedule, active, notes | create/edit, medication ID or new |
| `health.research` | title, content, tags, sourceUrl | create/edit, note ID or new |
| `health.symptom` | name, severity, occurredAt, notes | create/edit, symptom ID or new |
| `schedule.form` | name, cron, dispatchMode, prompt, jobName, jobArgsRaw, complexity | butler, create/edit, schedule ID or new |
| `approvals.rule.create` | tool_name, description, arg_constraints_text, max_uses, expires_days | create |
| `relationship.interaction` | type, summary | entity ID, action |
| `relationship.gift` | description, occasion | entity ID, action |
| `relationship.note` | content | entity ID, action |
| `chronicles.correction` | title when rendered, privacy, note | episode ID |
| `butler-state.set` | key, value JSON text | butler, create |
| `butler-state.edit` | key, value JSON text | butler, edit, target key |
| `calendar.butler-event` | butlerName, eventKind, title, startAtLocal, endAtLocal, timezone, recurrenceFrequency, cron, hasUntilAt, untilAtLocal | butler, operation, entry ID or new |

The retired `relationship.reach-out` identity remains in the typed
registry with message/channel fields but has no consumer: that action is retired.
No replacement action is registered. Medication supply quantity, the standing-rule
butler selector, participant display names/emails, page-context chips, inline
calendar autosave, and unrelated form fields are excluded. Currently sensitive
episode corrections neither persist nor restore the hidden title. Eligible
ordinary prose is not inspected or classified for secret-looking content.

## Persistence and concurrency

Native IndexedDB database `butlers-drafts`, version 1, owns `drafts`, reserved
`chatAttempts`, and `meta`. Keys contain the version, surface ID, and base64url
SHA-256 of canonical structural context, never user prose. Records are whole
validated bundles. Each distinct edit receives a random immutable
`contentRevision`; `(storeEpoch, writeRevision)` is the separate mutable fence.
A read-write transaction compares that fence before accepting a write. Each
mounted task coalesces edits and verifies the accepted revision by reading back.

An accepted edit slides expiry to 24 hours. A read, restore, or confirmed close
does not extend it. The complete serialized record must fit 64 KiB; oversized
input stays in the form without truncation or replacement of the last committed
bundle. Expiry and invalid records are removed before exposure. Empty/reset,
submission success, explicit discard, and authentication reset produce ordered,
content-free tombstones. Empty or whitespace-only chat text is empty; meaningful
message bytes and form bundles stay exact. Legacy whitespace chat records retire
before restoration. Tombstones are compacted at 256 records or when the
oldest reaches 30 days. One transaction advances the epoch, preserves live
content identities and timestamps, and removes tombstones. Old writers remain
fenced even after deletion or compaction.

Edit tasks compare an available source revision/`updated_at`, otherwise the
SHA-256 of their initial eligible bundle. A mismatch renders current source
values first and offers **Load draft** or **Discard draft**. Create tasks restore
matching records automatically. Restore announces once and does not move focus.

BroadcastChannel messages contain only a change signal. Notifications, focus,
and visibility regain cause native reads. Untouched views adopt the durable
winner; locally edited views suspend writes and offer **Use this tab**, **Load
other draft**, or **Discard draft**. There is no field merge. An explicit
replacement rereads the current epoch and receives a new content revision even
for identical bytes; a subsequent epoch still refuses it. A successful submission
that finds another tab's successor adopts it before further editing. Reopening a
create form keeps its fresh-task baseline rather than treating prior unsent input
as initial data. Callback feedback is bound to the submitted view and revision.

## Dismissal and submission

Only enrolled dirty tasks participate in the real router's draft blocker. This
is the approved unsaved-form dismissal contract; it adds no navigation-intent
consumer, prefetch, unload prompt, or recovery behavior to unrelated routes.
Cancel, Escape, backdrop/close controls, and enrolled in-app navigation offer:

- **Keep editing**: retain the current view.
- **Keep draft and close**: confirm the latest committed revision by read-back.
- **Discard and close**: confirm an ordered tombstone by read-back.

Chat can close immediately after the same confirmation. Commit plus read-back
has a one-second outer bound. Blocked, denied, quota-limited, failed, or hung
storage leaves input editable and the view open with a truthful retry or
**Close with save/deletion unconfirmed** choice. A late storage callback cannot
close a retired task, a newer revision, or a successor view. Dialog content stays
within the viewport so the choices remain keyboard and pointer reachable.

Valid domain requests proceed once after the bounded storage attempt, even if
persistence is unavailable. Domain failure preserves the form. Proven success
clears only the exact submitted content revision, using a transaction against
the current epoch, including after unrelated compaction. Newer, reset, expired,
or byte-identically recreated revisions are protected. Browser deletion failure
has its own visible status and deletion-only retry; the accepted revision cannot
be submitted again from that view while cleanup is unresolved. Retrying cleanup
cannot delete another tab's successor revision.

## Authentication and rollback

Owner-session loss invokes the store's ordered reset and retires the private
views. Authentication still ends when storage is unavailable; this does not
claim that unavailable storage was erased. For rollback, first run the existing
`DraftStore.reset()` retirement transaction in the still-installed application,
confirm it, then remove registrations and the reader/writer. Attempt database
deletion only after application connections close. Advance the epoch and leave
content-free tombstones when deletion cannot be confirmed. Never simply remove
the reader while live records remain.
