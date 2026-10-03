## Why

The owner has no way to say once that someone has died, is estranged or must not be contacted.
`entities.listed = false` is the only lever and it hides the person's memories and chronicles too.
A butler system that wishes a dead friend happy birthday, asks Finance for a gift budget for them,
or nudges the owner to reconnect with them is the clearest trust failure the system can produce
(bu-q7vx1q.8, JARVIS pursuit run 15).

## What changes

- `public.entities` gains an owner-asserted `posture` (`active`, `memorial`, `quiet`,
  `no_contact`; default `active`), `posture_since` and `posture_set_by`. `listed` stays the separate
  "hide from search" axis. Posture is never inferred and nothing is deleted.
- Relationship's `entity_set_posture` MCP tool is the only writer; a trigger on `public.entities`
  refuses a posture change from any other butler runtime role.
- Producers that nudge about a person read only `active` people: the relationship briefing
  (birthdays, reconnection gaps, the Finance gift ask), the relationship insight scan, and the
  overdue-contacts tool. The calendar overlay turns a memorial person's birthday into a low-priority
  `remembrance` entry and drops a quiet or no_contact person's dates.
- `notify(entity_id=...)` refuses with code `recipient_posture` for memorial and no_contact
  recipients, and when the posture cannot be read, before any identifier lookup, approval parking or
  delivery row.

## Deferred

- Finance cost-claim reconciliation raising one owner question for an open loan claim against a
  memorial counterparty.
- The anniversary-of-passing occasion (waits for the occasion engine's loss tone).
- A dashboard surface for posture (`dashboard-relationship`).
- An owner opt-in toggle for the remembrance entry; the first slice always emits it for memorial.
