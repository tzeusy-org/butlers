## Why

The Timeline currently preserves rows from reachable sources but reduces a failed
session fan-out to a generic source label, silently treats unavailable butler
facets and saved views as empty, and loses a failed older-page request. The
Sessions pinned strip similarly conflates an error-detail query that is loading,
failed, or successfully returned `null`.

The dashboard must preserve usable evidence while making the unavailable
boundary and retry path explicit; a partial response must never imply a complete
fleet history, an absent filter option, or a known lack of error detail.

## What Changes

- Add an additive `degraded_butlers` field to Timeline response metadata for
  named failed session fan-out pools while retaining existing generic
  `degraded_sources` semantics.
- Surface named partial-session, butler-facet, saved-view, and older-page
  failures in the Timeline without discarding already usable rows or built-in
  controls.
- Preserve the exact pagination cursor after an older-page failure and offer a
  retry that reuses that cursor.
- Represent each pinned failure excerpt as loading, unavailable with an
  individual retry, or loaded with a known error value (including known-null).

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-visibility`: Timeline partial-source metadata, reader-visible
  unavailable states, retry behavior, and pinned session-excerpt states become
  explicit.

## Impact

- Backend Timeline response models and router metadata only; no change to
  `DatabaseManager` fan-out behavior, global endpoints, persistence, or schema.
- Frontend Timeline API types, ledger hook/page/ledger, saved-view reader
  treatment, and pinned-session error excerpt rendering.
- Focused API and frontend behavior tests plus one strict
  `dashboard-visibility` delta validation.
