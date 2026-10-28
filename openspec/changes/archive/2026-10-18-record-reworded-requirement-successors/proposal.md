## Why

The archived-requirements ratchet (`scripts/archived-requirements-baseline.json`) still reads 15
requirements as never landed because they were reworded into a successor in the same capability and
no change recorded the rename. The guard matches requirement headers by exact name, so a reworded
header looks missing. This is slice 2 of the bu-tk618 burn-down (bu-4069d4).

## What Changes

- Record 15 predecessor-to-successor mappings as one `RENAMED`-only delta across 12 capabilities.
  Every successor already exists in the canonical spec and strictly supersedes the predecessor
  (identifier retirements such as `contact_id` to `entity_id`, `shared` to `public` schema names,
  transport changes, and tighter wording).
- No canonical requirement body changes; the archive step leaves every spec byte-identical.
- Delete exactly the 15 healed predecessor keys from the ratchet by hand.

## Impact

- `openspec/changes/archive/` gains the archived change; `scripts/archived-requirements-baseline.json`
  loses 15 keys. Candidates whose bodies differ materially, or whose archived block was a MODIFIED
  rewrite, stay frozen (see the PR body).
