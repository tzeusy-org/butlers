## Why

Connector-detail routing rules all navigate to the bare Filters route, leaving the owner to find the referenced rule. An exact navigation target must remain faithful when rules are disabled, archived, or unavailable.

## What Changes

- Link each connector routing-rule row to its URL-encoded rule id.
- Locate, highlight, scroll to, and accessibly focus the exact active or archived row once per target change.
- Distinguish a confirmed absent rule from loading, incomplete, and failed reads; offer retry for unavailable reads.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-ingestion-dispatch-console`: add Connector routing-rule deep-link fidelity beside the baseline Connector Detail and Filters Pipeline requirements.

## Impact

Seven ingestion component/test paths only; no API or runtime action. This ADDED requirement composes with `restore-ingestion-console-spec-coverage`, including its Filter Control Persistence and Summary-Only Roster Polling requirements. `rule` is a navigation focus target, not URL-backed filter state. Interaction affordances follow `dashboard-design-language`.
