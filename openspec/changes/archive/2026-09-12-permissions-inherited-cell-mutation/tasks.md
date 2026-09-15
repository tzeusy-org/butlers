## 1. Permission mutation boundary

- [x] 1.1 Reject PUTs for permissions outside `ENFORCED_PERMISSIONS` before any write.
- [x] 1.2 Verify the target butler exists in `butler_registry` before the UPSERT,
  audit append, or webhook dispatch.

## 2. Inherited-cell interaction

- [x] 2.1 Keep inherited cells visibly dim but enabled, keyboard-operable, and
  routed through the existing reason modal.
- [x] 2.2 Optimistically render a confirmed first override as explicit and
  restore the inherited cell when the mutation fails.

## 3. Regression protection and verification

- [x] 3.1 Add focused API regressions for unknown permission/butler rejection
  and no-write behavior.
- [x] 3.2 Add focused frontend regressions for inherited-cell grant/revoke,
  modal payload, and dim-to-foreground transition.
- [x] 3.3 Run strict OpenSpec validation and the risk-scaled backend/frontend
  quality gates.
