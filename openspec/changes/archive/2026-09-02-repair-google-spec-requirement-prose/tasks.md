## 1. Repair the defective Google requirements

- [x] 1.1 Add RFC-2119 prose to the three prose-less requirements in each of `connector-google-calendar` and `connector-google-drive`, adapting rather than copying the Gmail wording.
- [x] 1.2 Add RFC-2119 prose to the five prose-less `connector-google-health` requirements.
- [x] 1.3 Add a scenario to `connector-google-health` / `Structural Cost Gates Not Applicable` restating the `SHALL NOT` its prose already carries.
- [x] 1.4 Add RFC-2119 prose to the five `google-multi-account-oauth`, three `google-account-registry`, and two `dashboard-google-accounts` prose-less requirements.
- [x] 1.5 Add RFC-2119 prose to the four `module-google-drive` and six `module-google-health` prose-less requirements.

## 2. Verification

- [x] 2.1 `openspec validate repair-google-spec-requirement-prose --strict` passes.
- [x] 2.2 In a scratch copy, archive this change and confirm all eight rebuilt specs validate with no `✗` errors.
- [x] 2.3 Confirm each rebuilt baseline differs from the original only by the added prose and the one added scenario, and that `check_spec_overwrites.py` reports an unchanged debt count.
