## 1. Prove the absence first

- [x] 1.1 Add an absence-sentinel test to
  `tests/api/test_secrets_v2_audit_history.py`: seed an audit row whose note is
  an obviously-synthetic stand-in generated in-test, and assert it appears
  nowhere in the endpoint's response bytes. The sentinel is generated per run,
  never written as a literal — a test proving free text absent must not spell
  that text out.
- [x] 1.2 Pin the absence at the model too, so the field has no wire
  representation to reintroduce.

## 2. Project the endpoint

- [x] 2.1 Drop `note` from `AuditEvent`.
- [x] 2.2 Drop `note` from the `get_audit_history` `SELECT`, so the free text is
  never even read into the response path.
- [x] 2.3 Correct the `AuditEvent` docstring, which said "verbatim stored note"
  and was the reason review read the passthrough as intended.
- [x] 2.4 Drop `note` from `SecretsAuditEvent` in `frontend/src/api/types.ts`.

## 3. Write the contract

- [x] 3.1 Carry the new wire shape as a `## MODIFIED Requirements` block for
  `Secrets Audit-History and Breaks-Catalogue Endpoints`, reproducing the whole
  requirement including every scenario heading verbatim — archive writes the
  whole requirement into the baseline and openspec matches scenarios by name
  only, so a dropped heading silently deletes that scenario.
- [x] 3.2 Verify no other unarchived change holds a block on this requirement:
  `rg -l '^### Requirement: Secrets Audit-History and Breaks-Catalogue Endpoints$' openspec/changes/*/specs/*/spec.md`

## 4. Close out

- [ ] 4.1 After merge, apply the delta to `openspec/specs/dashboard-api/spec.md`
  and archive to
  `openspec/changes/archive/YYYY-MM-DD-project-audit-history-content-blind`.
