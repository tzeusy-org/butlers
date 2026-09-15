## 1. Focused regression coverage

- [x] 1.1 Add failing entity-detail tests for valid replace navigation, accessible announcement, normal/archived non-navigation, and malformed/self merge-metadata inconsistency states.
- [x] 1.2 Add failing merge-review dialog tests proving the final alert-style confirmation names both entities, Cancel/Escape make no request, and Confirm preserves the existing merge payload.

## 2. Frontend implementation

- [x] 2.1 Validate `metadata.merged_into` locally in `EntityDetailPage`, replace-navigate only to a valid distinct survivor, announce the transition, and suppress source detail/actions while redirecting.
- [x] 2.2 Render a named alert-level inconsistency for malformed, blank, or self-referential merge metadata without redirecting.
- [x] 2.3 Add a controlled final `AlertDialog` to `MergeCompareDialog` that gates the existing merge handler without changing its payload, comparison prerequisite, or error handling.

## 3. Verification and handoff

- [x] 3.1 Run the focused entity tombstone and merge-dialog test files, including keyboard and screen-reader assertions.
- [x] 3.2 Run frontend lint and build, strict OpenSpec validation, and a scoped diff review confirming no API/router/migration/ACL changes.
