## Why

The existing Relationship tool-surface requirement mixes a historical broad
capability inventory with literal MCP registration names. It simultaneously
names entity handlers as available and requires them to remain pruned, so it
cannot govern the current runtime without contradiction.

## What Changes

- Retire the ambiguous historical requirement.
- Replace it with an exact current registered-surface requirement: eight active
  groups own 58 tools, plus the unconditional approval-dispatch handler.
- Keep every Relationship entity handler absent until the separately adopted
  read-only split is implemented.
- Preserve the historical audit text in its existing archived change rather
  than rewriting that record to current truth.

## Capabilities

### Modified Capabilities

- `butler-relationship`: replace an ambiguous inventory contract with an exact
  registered-tool contract matching the implemented roster.

## Impact

This is the spec lifecycle record for the Relationship roster correction in PR
4160. It adds no runtime behavior, migration, restart, deployment, or live-data
access beyond that reviewed implementation.
