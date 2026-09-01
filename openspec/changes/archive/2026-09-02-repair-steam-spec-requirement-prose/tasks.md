## 1. Repair the prose-less Steam requirements

- [x] 1.1 Add RFC-2119 prose to the ten prose-less `module-steam` tool requirements, carrying every scenario unchanged.
- [x] 1.2 Add RFC-2119 prose to `dashboard-steam` / `Account Status Endpoint`, `Connector Health View`, and `Dashboard UI Components`.
- [x] 1.3 Add RFC-2119 prose to `steam-account-registry` / `Account Lifecycle Management`.

## 2. Verification

- [x] 2.1 `openspec validate repair-steam-spec-requirement-prose --strict` passes.
- [x] 2.2 In a scratch copy, archive this change and confirm all three rebuilt specs validate with no `✗` errors.
- [x] 2.3 Confirm each rebuilt baseline differs from the original only by the added prose — no scenario dropped or reworded — and that `check_spec_overwrites.py` reports an unchanged debt count.
