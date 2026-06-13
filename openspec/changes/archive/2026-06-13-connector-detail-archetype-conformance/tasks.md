> ⛔ **WITHDRAWN — SUPERSEDED (2026-06-13, bead `bu-kondu`).** These tasks are
> obsolete: `ConnectorDetailPage` was redesigned off the `<DetailPage>` archetype
> onto the Dispatch console layout (PRs #1941, #1956). The delta was never synced
> into `connector-base-spec`. See `proposal.md` for the full withdrawal rationale.
> No task here will be completed. Preserved for history only.

## 1. Spec Authoring

- [x] 1.1 Decide ConnectorDetailPage spec home (connector-base-spec chosen; see proposal.md).
- [x] 1.2 Write delta spec: `specs/connector-base-spec/spec.md` with
      archetype conformance requirement and slot mappings.
- [x] 1.3 Run `openspec validate connector-detail-archetype-conformance` and confirm pass.

## 2. Verification

- [x] 2.1 Verify `frontend/src/pages/ConnectorDetailPage.tsx` uses `<DetailPage>` with
      `pulse`, `primary`, and `auxiliary` slots matching the delta spec (code-only check,
      no implementation change needed).
- [x] 2.2 Confirm `openspec/specs/connector-base-spec/spec.md` does not already contain
      an archetype conformance requirement (it does not as of the commit that merges this
      change).

## 3. Open Items

- [ ] 3.1 `practical` slot for ConnectorDetailPage — the delta spec reserves this for
      reset / delete actions (destructive operator controls). Current implementation
      has no practical drawer. File a follow-up bead when these controls are added.
- [ ] 3.2 `pulse` slot — currently `null` in the implementation. Ingest health strip
      (liveness badge, last heartbeat age, today's count) is the natural pulse content.
      File a follow-up bead for the PulseStrip implementation.
