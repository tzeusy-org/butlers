## 1. Implementation

- [x] 1.1 Advisory lock around the sweep; endpoint 429 and job skip
- [x] 1.2 Explicit verify-all timeout in the dashboard client
- [x] 1.3 Exclusion tests in new files and a client timeout unit test
- [x] 1.4 `docs/runtime/model-routing.md` verify-all note

## 2. Verification

- [x] 2.1 `openspec validate --all --strict`
- [ ] 2.2 Contract-evolution manifest binds the retained fixture edits to `REQ-dashboard-model-settings-003`
