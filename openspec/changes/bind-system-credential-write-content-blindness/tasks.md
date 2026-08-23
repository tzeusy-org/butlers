## 1. Project the write response

- [x] 1.1 Rewire `set_system_credential` to `response_model=ApiResponse[SystemCredentialDetail]`
  and project through the existing `_content_blind_system_detail` at all three
  return sites, treating the projector as read-only rather than reshaping it.
- [x] 1.2 Keep the override branch's `row_state` / `target` marking by applying
  it to the record before projecting.
- [x] 1.3 Update the record and projector docstrings: `SystemSecretDetail` is
  now internal-only, and the projector is the sole path from it to a client.

## 2. Prove the absence

- [x] 2.1 Endpoint sentinel test for the free text this route can actually
  plant (`last_test_message`, probe `message`).
- [x] 2.2 Endpoint test pinning the published field set to
  `SystemCredentialDetail.model_fields`, so `breaks` cannot return.
- [x] 2.3 Endpoint test for the audit `note` and a `breaks` entry (feature
  label + a raw scope) with the fetch helper stubbed — it populates neither
  field today, so asserting against the real helper would pass without
  exercising anything, while the stub still fails if a future fetch fills them.
- [x] 2.4 Cover the `shared-public` and per-butler override branches, not just
  the shared one.

## 3. Write the contract

- [x] 3.1 Carry the wire shape as a `## MODIFIED Requirements` block for
  `Secrets Mutation Endpoints`, reproducing the whole requirement including all
  five scenario headings verbatim — archive writes the whole requirement into
  the baseline and openspec matches scenarios by name only.
- [x] 3.2 Verify no other unarchived change holds a block on this requirement.

## 4. Close out

- [ ] 4.1 After merge, apply the delta to `openspec/specs/dashboard-api/spec.md`
  and archive to
  `openspec/changes/archive/YYYY-MM-DD-bind-system-credential-write-content-blindness`.
