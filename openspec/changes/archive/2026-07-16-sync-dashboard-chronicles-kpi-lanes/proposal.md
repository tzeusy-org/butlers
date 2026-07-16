## Why

The Dashboard Chronicles KPI requirement names `hours_by_top_lanes` but does not bind its values to the shipped activity-lane vocabulary. That omission leaves room for the obsolete ten-category model even though the API and main category-taxonomy requirement now use the IEA-derived lanes plus `butler_ops`.

## What Changes

- Clarify that `hours_by_top_lanes[*].lane` uses the Activity lane taxonomy: `sleep`, `exercise`, `work`, `butler_ops`, `play`, `social`, `travel`, `eat`, and `rest`.
- Specify that the KPI contains only activity-layer lanes, preserving the distinction from intent and evidence records.
- Document that `butler_ops` is the separately tracked internal-butler lane, not owner occupation work.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chronicles`: Bind the editorial KPI response's top-lane values to the existing Activity lane contract.

## Impact

This is a documentation-only OpenSpec correction. It changes no runtime code, API payload shape, database schema, or dependencies.
