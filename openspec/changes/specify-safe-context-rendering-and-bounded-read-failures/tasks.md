## 1. Contract authoring and verification

- [x] 1.1 Inspect canonical bu-dhloi1.17/bu-dhloi1.2, doctrine, RFC0009/RFC0038 and actual producer/renderer/attention/binding seams.
- [x] 1.2 Draft complete typed rendering, bound owner mutation and finite read-failure/expiry/recovery scenarios without new signals/timezones/durations.
- [x] 1.3 Preserve all existing MODIFIED scenario names and unaffected producer/DND behavior; record every changed requirement's implementation/test seam and zero test-source delta.
- [x] 1.4 Run strict OpenSpec, requirement trace, referenced-path and current required static checks; retain actual receipts.
- [ ] 1.5 Obtain independent normative/conformance review of exact final text and resolve confirmed findings.

## 2. Exact-text adoption and handoff

- [ ] 2.1 Record owner adoption identity/date/immutable commit and RFC0009 affected sections after concrete text and independent review exist; do not infer adoption from scheduling release.
- [ ] 2.2 Land the adopted contract through the ordinary protected PR route, then hand the satisfied prerequisite to bu-dhloi1.2.

Runtime implementation, changed runtime tests, switch execution, deployment and activation are outside this task. The unchecked adoption/landing steps are genuine later gates, not implementation claims or permission requests in this draft.

Verification qualification: named and all-change strict OpenSpec, repository requirement trace and referenced paths pass. Whole guards remain NONPASS at the spec-overwrite ratchet because this proposed amendment intentionally replaces contradictory baseline clauses; no unadopted losses were ratcheted. The global authoring trace also retains unrelated pre-existing debt; the five new/modified requirements pass the scoped authoring check. Independent review and exact-text adoption remain unchecked.
