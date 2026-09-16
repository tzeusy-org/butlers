## 1. Calendar module and API boundary

- [x] 1.1 Preserve and test the Calendar module structured free/busy error
  dictionary, including sanitized diagnostics and empty slots.
- [x] 1.2 Detect `status="error"` before slot-list handling in the workspace
  route and return the existing typed unavailable envelope with a fixed safe
  reason.
- [x] 1.3 Keep successful `slots=[]` responses available and backward-compatible.

## 2. Calendar Workspace and specification

- [x] 2.1 Add focused API and Calendar Workspace tests for structured failure and
  genuine zero-slot success.
- [x] 2.2 Add strict, overwrite-safe deltas for the module-calendar Find Free
  Slots Tool and dashboard-domain-pages Calendar Workspace requirement.
- [ ] 2.3 Run focused tests, test planning, guards, strict OpenSpec validation,
  and the applicable frontend CI order.
