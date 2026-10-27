# Accepted S2 implementation

## Context

The coordinator accepted the already shaped S2 allocation following actual
scanner/ABC registrar release and the Calendar S1/S5 merges. This implements
that allocation, not a new response owner or evidence-protocol decision.

## Decisions

- Use the existing ToolGroupMixin contract: absent/empty groups select all;
  an explicit list selects only its named groups. `response_enabled` remains
  the opt-in execution switch. An explicit core-only list with responses
  enabled is a descriptive configuration error, and both actual rosters migrate.
- Response-only means response selected with neither core nor butler_events.
  Return after canonical credential/account resolution and real lazy provider
  construction, before the first calendar-ID/discovery call. Normal startup,
  including force-sync when periodic sync is disabled, is unchanged.
- Keep the directly registered handler, private excluded bindings, canonical
  preparer and captured owning executor. Admission resolves the exact eligible
  projection/account independently of discovery caches. It never repairs missing
  source state. The existing successful effect may still update its projection.
- Retain Messenger and Relationship response writers and direct historical
  status reads, including execution-disabled registration. No S3 fence/route
  retirement or S4 mutation/gating work is included.

## Verification

Extend the existing real-daemon response node to execute the actual lifecycle
startup phase with canonical credential resolution and the real lazy provider.
Raising post-provider spies and task/HTTP/write assertions distinguish isolation
from a disabled polling flag. Preserve normal startup/queue/hygiene populations,
all existing response authority cases, real catalog/private schemas and the
existing package/register_tools size gate. Extend the existing surface matrix
with the explicit response-only and full group inputs; retain the prior explicit
core-only input as the intentional missing-response-group refusal.
