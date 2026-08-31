## 1. Write the contract

- [x] 1.1 Add `## ADDED Requirements` → `Content-Blind Secrets Probe and
  Reauthorize Mutations` to `specs/dashboard-api/spec.md`, naming
  `PROBE_FAILURE_VOCABULARY` and the capability vocabulary as the only permitted
  evidence and `account_ref` as a system-issued identity rather than a hint read
  out of the credential row.
- [x] 1.2 Confirm the requirement describes shipped behaviour rather than
  proposing new behaviour: `src/butlers/api/routers/secrets_v2.py`
  (`_probe_failure_category`, `_probe_category`, `_capability_name`, the
  reauthorize `account_ref` parameter build),
  `src/butlers/api/routers/oauth.py` (`_resolve_account_ref_hint`), and
  `tests/api/test_secrets_v2_probe_reauthorize_content_blind.py`.
- [x] 1.3 Verify the requirement is `ADDED`, not `MODIFIED`, so archive cannot
  drop a clause from `Secrets Mutation Endpoints` or from the read-side
  content-blind requirements.

## 2. Close out

- [x] 2.1 After merge, apply the delta to `openspec/specs/dashboard-api/spec.md`
  and archive the change to `openspec/changes/archive/YYYY-MM-DD-bind-secret-mutation-content-blindness`.
  A spec amendment that merges un-applied leaves the baseline silent, which is
  the exact failure mode this change exists to fix.
