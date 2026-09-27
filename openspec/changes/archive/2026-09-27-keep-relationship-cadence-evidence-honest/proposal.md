## Why

The Relationship entity pulse labels a rolling cadence window but derives its count from the
first page of a mixed-kind timeline. A page containing no interactions can therefore render
"Quiet" even when additional interaction evidence exists beyond the page, and the fixed label can
drift from a refreshed query window.

## What Changes

- Add a bounded Relationship cadence read that echoes the exact rolling window and explicitly
  distinguishes complete evidence from a capped, paginated subset.
- Derive the PulseStrip cadence label and value from that matching evidence.
- Reserve "Quiet" for a complete zero-interaction result; render typed attention for incomplete,
  mismatched, or unavailable evidence.
- Preserve the existing cadence policy, timeline API, relationship ranking, and provider boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-domain-pages`: make Relationship cadence-window labels and calm states conditional on
  complete, matching evidence.

## Impact

- Relationship API models/router gain one read-only entity cadence projection.
- The frontend API client, entity hooks, and `PulseStrip` consume that projection.
- Focused Relationship API and PulseStrip tests cover complete, paginated, error, and refreshed
  window behavior. No live provider or relationship mutation is authorized.
