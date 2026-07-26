## Context

`Spawner.cancel_session()` records the exact owner-cancellation marker in the
existing `sessions.error` column. The detail API returns that field and the
detail status badge recognizes it, but both session-list routes use a compact
summary projection that omits the outcome. The table therefore renders every
unsuccessful list row as `Failed`.

## Goals / Non-Goals

**Goals:**

- Carry only a boolean `cancelled_by_owner` discriminator through the summary
  read model, API DTO, frontend type, and `SessionTable`.
- Derive the discriminator only from the exact canonical marker paired with a
  failed terminal outcome.
- Keep the cross-butler keyset and butler-scoped offset list routes coherent.

**Non-Goals:**

- Expose `sessions.error` or any other raw error text in list responses.
- Add a status enum, migration, list filter, new cancellation transport, or
  change session-detail and Stop behavior.

## Decisions

- Compute the boolean in the summary SQL projection rather than adding raw
  `error` to the summary DTO. This preserves the list privacy boundary and
  prevents generic error text from reaching response serialization.
- Match the existing `SESSION_CANCELLED_ERROR` value exactly, with
  `success IS FALSE`, rather than using a substring or an arbitrary failed
  error. This makes only the owner-confirmed outcome cancellable in display.
- Let `StatusBadge` accept the boolean in addition to its existing detail
  `error` input. `SessionTable` supplies the boolean; detail callers retain
  their established marker behavior.

## Risks / Trade-offs

- [A future marker change could drift from the projection] -> Build the SQL
  projection from the backend canonical constant and cover canonical,
  generic-failure, and non-terminal cases in route tests.
- [Older frontend fixtures can lack the additive field] -> Update typed
  fixtures to carry false explicitly, matching the current API contract.

## Migration Plan

Deploy as an additive response field. Older clients ignore it; newer clients
render `Cancelled` only when it is true. No data migration or rollback step is
required; reverting the consumer restores the former `Failed` rendering.
