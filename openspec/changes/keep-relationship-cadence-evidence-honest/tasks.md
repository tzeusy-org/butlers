## 1. Contract

- [x] 1.1 Define matching cadence window, completeness, pagination, and error semantics in the full
  dashboard-domain-pages requirement.
- [x] 1.2 Preserve every baseline scenario in the MODIFIED requirement and run strict/overwrite
  validation.

## 2. Relationship cadence projection

- [x] 2.1 Add the bounded, read-only entity cadence response and endpoint.
- [x] 2.2 Echo server-captured window bounds and expose complete versus capped evidence.
- [x] 2.3 Add focused Relationship API coverage for complete and incomplete evidence.

## 3. PulseStrip truthfulness

- [x] 3.1 Query cadence evidence by entity and window rather than deriving it from a mixed timeline
  page.
- [x] 3.2 Render "Quiet" only for matching complete zero evidence and typed attention for
  incomplete, mismatched, or failed evidence.
- [x] 3.3 Extend PulseStrip tests for calm, attention, and window-refresh behavior.

## 4. Verification and handoff

- [x] 4.1 Run focused Relationship API/PulseStrip tests, test-plan, guards, spec gates, and the
  frontend CI command order including knip.
- [x] 4.2 Review the final diff for scope: no cadence policy, social inference, provider access,
  ranking, relationship mutation, or unrelated redesign.
- [ ] 4.3 Push the exact head, open a PR, and record hosted CI plus the test delta.
