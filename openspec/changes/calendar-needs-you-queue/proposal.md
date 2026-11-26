## Why

What needs the owner on `/calendar` is spread over four surfaces: unanswered invitations, overlap
issues, butler-extracted proposals and pending meeting debriefs. Each has its own panel, its own
keys (or none) and its own count, so "what do I need to do today?" takes four looks, and a failed
read can silently make a list look empty. bu-5ii4gj.11 (calendar pursuit run 26, track N) asks for
one keyboard queue.

## What changes

- **One queue hook.** `use-needs-you-queue.ts` composes the invitations, conflicts, proposals and
  debriefs reads the page already makes into one ordered list of items and one per-kind readiness
  map. It adds no endpoint and no store.
- **One panel.** A Needs-you panel on the calendar page lists the items in urgency order and is
  driven by the shared `useListTriage` hook, so `j`/`k`, the per-type verbs, the `?` help sheet and
  the command palette come from the one shortcut registry.
- **Honest counts.** The verdict clause counts the queue, and a kind whose read failed or is
  partial is named as unknown instead of being counted as empty.

## Out of scope

The changes feed (provider moves, cancellations and location changes as before/after items) is N3
and needs a stored diff; the queue declares that kind unknown until then. A persisted `x` dismiss
receipt, conflict resolve keys and a command-palette verb per kind wait on the resolve route
and its acknowledgement store. No new reasoning agent and no replacement for the notification
delivery ledger.

## Slices

N2a this spec; N2b the hook, the panel and its tests.
