## Why

"Last met", cadence and series memory are read from invitations. A declined, moved or never-held
meeting reads as a meeting that happened (bu-5ii4gj.3, calendar pursuit run 26 move 3). E1 stopped
the two existing readers counting plans with an interim rule ("the owner accepted and it started").
The encounter ledger replaces that rule with one stored state per person and per occurrence, so
only a meeting somebody vouches for counts as met.

## What changes (slice E2a, dark)

- A migration adds `relationship.encounters` and widens `meeting_debriefs.state` with
  `did_not_happen` and `moved`. The widening drops and recreates the CHECK by name; the downgrade
  maps those two states to `expired` and restores the four-state CHECK.
- A deterministic reducer recomputes each row from the occurrence's participant records, the
  event's status and start, and the owner's debrief answer. A replay converges. An unreadable input
  leaves every prior row unchanged and the run reports `complete: false`.
- Nothing schedules the reducer and no reader consults the table. There is no user-visible change.

## Not in this change (E2b and later)

- `meeting_debrief_answer` gaining the `did_not_happen` and `moved` outcomes.
- `calendar_prep`, the meeting card, `interaction_sync`, Dunbar and stale-contact readers switching
  to the ledger, the job's schedule, and retraction of existing mutual rows with a recorded verdict.
- Chronicler corroboration (`happened_corroborated`, basis `chronicler`) and series memory.

## Debrief stays on its own resolver (decision)

The debrief's private people resolver withholds tombstoned (`no_contact`, `memorial`) entities whose
`has-email` facts stay active. The participant definer excludes merged, deleted and unidentified
entities only, so moving the debrief onto participant records would expose them as unresolved
attendees with an address and a display name in the stored snapshot. The debrief therefore keeps its
resolver; the ledger reads participants and never replaces it.
