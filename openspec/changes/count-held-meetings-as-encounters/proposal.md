## Why

"Last met" and the passive calendar interactions are written from invitations: a declined or
never-held meeting, a native reminder or a guest who said no sets "last met" and logs a mutual
interaction (bu-5ii4gj.3, calendar pursuit run 26 move 3, encounter ledger). S1 gave every
occurrence a participant record; this slice makes the two existing readers stop counting plans.

## What changes

- `interaction_sync` reads attendees from the participant records, not the invitation's
  addresses, and logs an interaction (and mints a co-attended edge) only for an occurrence the owner
  accepted and that has started, for guests that resolved exactly or by manual link and did not
  decline. A provider event whose participants are not resolved yet logs nothing, holds the
  checkpoint and is seen again next run; there is no fallback to the invitation's addresses.
- The relationship prep job's "last met" skips native reminders, events the owner declined or did
  not accept, and events that attendee declined. Prep shows no "last met" it cannot stand behind.
- INTERIM: "owner accepted and started" stands in for "it happened". The encounter ledger
  (`relationship.encounters`, `did_not_happen`, `moved`, debrief outcomes, "last scheduled") replaces
  it in later slices.

## Not in this change

No migration and no table. Existing `calendar_event` interactions are not rewritten or retracted
(that needs a recorded migration verdict). Dunbar and stale-contact weighting rules are unchanged:
their inputs shrink, their formulas do not.
