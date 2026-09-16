## 1. Health API contract

- [x] 1.1 Make create/update quantity request fields strict positive integers.
- [x] 1.2 Preserve existing server quantity/timestamp response projection.
- [x] 1.3 Add create, update/refill, unknown, and typed-refusal API tests.

## 2. Dashboard seam

- [x] 2.1 Add quantity fields to frontend medication and request types.
- [x] 2.2 Add optional quantity input and explicit validation to MedicationForm.
- [x] 2.3 Wire create, edit, and refill quantity payloads without clearing an
      existing count on a blank edit.
- [x] 2.4 Render the exact count or `Supply: unknown` in medication rows.
- [x] 2.5 Add focused form/rendering tests.

## 3. Spec and verification

- [x] 3.1 Run focused Python/frontend tests, `make test-plan`, guards, and the
      frontend CI order including knip.
