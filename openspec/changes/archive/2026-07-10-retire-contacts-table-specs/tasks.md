## 1. Author removal deltas

- [x] 1.1 Write `## REMOVED Requirements` delta for `contacts-identity` (16 requirements) with Reason + Migration each
- [x] 1.2 Write `## REMOVED Requirements` delta for `module-contacts` ("Public Schema Tables") with Reason + Migration

## 2. Fix pre-existing canonical drift (unblock archive)

- [x] 2.1 Add `## Purpose` to canonical `contacts-identity` and tighten the reality note to match post-archival contents
- [x] 2.2 Add SHALL/MUST keyword to `module-contacts` "Cross-Provider Contact Backfill" and "[TARGET-STATE] Apple/CardDAV Provider" (pre-existing drift blocking archive)

## 3. Validate and archive

- [x] 3.1 `openspec validate retire-contacts-table-specs --strict` green
- [x] 3.2 `openspec archive retire-contacts-table-specs -y` (merge removals into canonical specs)
- [x] 3.3 `openspec validate contacts-identity --strict` green
- [x] 3.4 `openspec validate module-contacts --strict` green
