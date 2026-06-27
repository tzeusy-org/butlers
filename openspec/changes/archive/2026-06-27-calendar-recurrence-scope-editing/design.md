# Design — Recurrence-scope editing (this / following / series)

## Context

Recurring-event mutation is series-only. The tools carry
`recurrence_scope: Literal["series"] = "series"`
(`calendar_update_event` ~:3075, `calendar_delete_event` ~:3414) and the spec
pins butler-event deletion as "(series-scoped in v1)". The provider already
models recurrence as a `recurrence` array of RRULE strings (`~:917`, `~:1036`),
and the projection table `calendar_event_instances` already carries an
`is_exception` boolean (`~:6219`, `~:6240`). This change adds occurrence-level
scope without new storage.

## Decisions

### D1 — Widen the literal AND add explicit instance tools

Two surfaces, complementary:

- **Widen `recurrence_scope`** on the existing `calendar_update_event` /
  `calendar_delete_event` from `Literal["series"]` to
  `Literal["this", "following", "series"]`. This is the LLM-ergonomic path: the
  model already calls these tools and just supplies a scope.
- **Add `calendar_update_event_instance` / `calendar_delete_event_instance`** as
  the explicit occurrence-targeted entry points keyed by base `event_id` +
  `instance_start_at`. These make the "operate on exactly this occurrence"
  contract unambiguous (no `recurrence_scope` ambiguity about WHICH occurrence
  `this` means) and are the tools the impact-preview/exception machinery is
  documented against.

Rejected alternative: overload only `recurrence_scope` with an extra
`instance_start_at` arg on the existing tools and add no new tools. Rejected
because the contract drift the prompt calls out is specifically "ADDS
`calendar_update_event_instance` + `calendar_delete_event_instance`" → tool
count 18; the explicit tools also keep the common whole-series path clean.

### D2 — Occurrence identity = base event id + occurrence start

A single occurrence is addressed by the base recurring `event_id` plus its
`instance_start_at` (the occurrence's original start). This matches Google's
`originalStartTime` instance model and the projection's
`(event_id, origin_instance_ref)` uniqueness, and avoids leaking per-instance
provider ids into the tool contract.

### D3 — `this` and `following` map to provider recurrence-array edits

- **`this`** (delete) → append an `EXDATE` entry for the occurrence to the
  series' `recurrence` array; the series RRULE is otherwise unchanged.
- **`this`** (update) → detach the occurrence as an exception: EXDATE the
  original slot and carry the edited fields onto the detached occurrence; mark
  the projection row `is_exception = true`.
- **`following`** → split the series at the boundary: bound the original RRULE
  with an `UNTIL` just before `instance_start_at`, and apply the mutation to the
  occurrence-and-onward remainder.
- **`series`** → unchanged whole-series behavior (today's path).

In all occurrence-scoped cases the projected `calendar_event_instances` row(s)
for the affected occurrence(s) are written with `is_exception = true` so the
unified view and downstream consumers see the divergence.

### D4 — Impact preview drives the high-impact gate

A scope-aware mutation computes the occurrence count it will touch (1 for
`this`; remaining-from-boundary for `following`; whole expanded series for
`series`) using the existing occurrence expander, and feeds that count to the
existing `_gate_high_impact_mutation` path (`~:7427`). `series` and `following`
on large series remain high-impact; `this` is low-impact.

## Risks / Trade-offs

- **Provider EXDATE/UNTIL timezone correctness.** EXDATE/UNTIL must match the
  occurrence's original start in the series timezone or Google silently ignores
  the exclusion. Mitigated by reusing the occurrence expander's
  `(starts_at, ends_at)` pairs (already timezone-correct) as the source of the
  EXDATE/UNTIL value.
- **Sync reconciliation of exceptions.** An occurrence exception-ed in Google
  outside the butler still arrives via the normal projection sync; this change
  only adds the butler-initiated write path. Go-forward; no backfill of
  historical exceptions.
- **`following` is a split, not a true "from here" provider primitive.** Google
  has no atomic "this and following" delete; the UNTIL-bound + remainder
  approach is the standard emulation and is what the impact preview reports.

## Test Strategy

- Unit: `recurrence_scope` accepts `this` / `following` / `series`; an invalid
  scope value is rejected.
- Unit: `calendar_delete_event_instance` appends an EXDATE for the named
  occurrence and marks the projection row `is_exception = true`.
- Unit: `calendar_update_event_instance` detaches the occurrence
  (`is_exception = true`) and applies edited fields only to that occurrence.
- Unit: `following` bounds the original RRULE with `UNTIL` and applies the
  mutation to the remainder.
- Unit: impact preview returns 1 for `this`, remaining-from-boundary for
  `following`, whole-series count for `series`.
- Integration (fake provider): delete-this leaves the rest of the series intact;
  delete-following removes the boundary occurrence and all later ones; series
  delete removes the whole series.
