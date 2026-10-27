## Why

The core (non-dashboard) specs had accumulated material that does not govern
behavior: owner PII in examples, bead ids and PR/commit narrative, `Scope:
v1-mandatory` markers, placeholder `TBD` Purposes, `[TARGET-STATE]` tags on
requirements that have shipped, `file.py:NNN` citations that rot on every edit,
private helper names, and boilerplate filler lines ("The implementation SHALL
provide the behavior described by this requirement."). Several contracts were
also stated in two places and had drifted apart, and a few capabilities only
described one-time migrations that have finished.

## What Changes

- Replace owner PII in spec examples with neutral placeholders.
- Retire the capabilities whose whole content is a completed one-time migration
  or a superseded design: `finance-data-migration`, `messenger-tracking-retirement`,
  `source-filter-registry`. The standing backfill contract and the unshipped
  mirror-retirement target from `finance-data-migration` move to
  `finance-transaction-schema`.
- Remove individual spent requirements (completed cut-overs, a finished secret-key
  rename, a duplicated adapter harness requirement), each re-verified against code.
- Single-home duplicated contracts: the Steam heartbeat defers to
  `connector-base-spec`; the finance schedule table lives in `butler-finance` and
  `finance-alerts` references it; the adapter test harness lives in
  `adapter-integration-testing`; the complexity tier vocabulary lives in
  `complexity-classification` "Complexity Enum"; the mastery transition set lives
  in `module-education-mind-map` "Mastery status state machine".
- Strip bead ids, PR/SHA narrative, `Scope:` lines, stale `[TARGET-STATE]` tags,
  line-number citations, private helper names, and filler lines; rename
  requirements and scenarios whose titles carried that narrative.
- Correct stale statements found while re-verifying (for example the spawner's
  default complexity is `workhorse`, and an empty catalog fails closed).
- Requirements that an active change also modifies are left untouched.

## Capabilities

### Modified Capabilities

Most non-dashboard capabilities receive wording-only MODIFIED deltas. Requirement
renames, removals, and additions are listed in the delta specs.

### Removed Capabilities

- `finance-data-migration`
- `messenger-tracking-retirement`
- `source-filter-registry`

## Impact

Specification text only. No runtime behavior, migration, or deployment change.
`openspec/specs/predicate-taxonomy.md` (a loose non-capability file duplicating
the predicate catalog) is removed and its references re-pointed.
