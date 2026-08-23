## 1. Project the two read endpoints

- [x] 1.1 Add `SystemCredentialDetail` + `_content_blind_system_detail` and
  `CliCredentialDetail` + `_content_blind_cli_detail` to
  `src/butlers/api/routers/secrets_v2.py`, as new published models beside the
  internal records rather than a reshape of `SystemSecretDetail` /
  `CliRuntimeDetail`, which the system mutation route still serialises.
- [x] 1.2 Rewire `get_system_credential` and `get_cli_credential` to the new
  `response_model`s and project at every return site.
- [x] 1.3 Rebuild audit rows through the existing `CredentialAuditOutcome`
  (`ts` / `actor` / `action`) so the free-text `note` has no path to the wire,
  while the writers keep persisting it.
- [x] 1.4 Publish CLI capabilities through `_capability_categories`, which
  filters through the fixed vocabulary, so no input string can reach the output.

## 2. Prove the absence

- [x] 2.1 Sentinel tests at the endpoint for the free text the routes can
  actually plant today (`last_test_message`, probe `message`).
- [x] 2.2 Sentinel tests at the projector for audit notes, `breaks[]` feature
  labels, and raw scopes. These are asserted against the projector rather than
  the route deliberately: the fetch helpers never populate those fields, so an
  endpoint-level assertion would pass without exercising anything — a test green
  for the wrong reason. Pinning the projector still fails if a future writer
  starts populating them.
- [x] 2.3 Update the existing envelope tests to assert the dropped fields are
  absent rather than present-and-empty.

## 3. Write the contract

- [x] 3.1 Carry the new wire shape as a `## MODIFIED Requirements` block for
  `Secrets Inventory and Per-Credential Read Endpoints`, reproducing the whole
  requirement including all six scenario headings verbatim — archive writes the
  whole requirement into the baseline and openspec matches scenarios by name
  only, so a dropped heading silently deletes that scenario.
- [x] 3.2 Verify no other unarchived change holds a block on this requirement:
  `rg -l '^### Requirement: Secrets Inventory and Per-Credential Read Endpoints$' openspec/changes/*/specs/*/spec.md`

## 4. Close out

- [ ] 4.1 After merge, apply the delta to `openspec/specs/dashboard-api/spec.md`
  and archive to
  `openspec/changes/archive/YYYY-MM-DD-project-secret-read-endpoints-content-blind`.
