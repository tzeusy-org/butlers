## 1. Spec artifact (this change)

- [x] 1.1 Amend the `dashboard-api` requirement `Secrets Inventory and Per-Credential Read
      Endpoints`, scenario `Inventory rows are content-blind`, to withhold `system[]` / `cli[]`
      `description` and `category` while retaining `key`, and to name the boundary as partial
      metadata minimization rather than content-blind identity.
- [x] 1.2 Restate the full requirement (all scenarios) in the `MODIFIED Requirements` block per
      this repo's overwrite-guard convention (`AGENTS.md` § "Two unarchived OpenSpec changes can
      silently overwrite each other"), changing only the targeted scenario text.
- [x] 1.3 Record the coexistence of `openspec/changes/project-secret-read-endpoints-content-blind`
      as an unarchived sibling change touching the same requirement, in `proposal.md`.
- [x] 1.4 Add the target-contract test ahead of implementation:
      `tests/api/test_secrets_v2_inventory.py::test_inventory_system_and_cli_rows_omit_description_and_category_but_retain_key`,
      marked `xfail(strict=True)`, planting distinct `description` / `category` sentinels for both
      the system and CLI families and asserting their absence (not nullness) from the parsed
      response while `key` survives.
- [x] 1.5 Confirm the new test currently `xfail`s (not errors, not unexpectedly passes) against
      today's handler, and that the full existing inventory test file remains green.

## 2. Validation (this change)

- [ ] 2.1 `openspec validate amend-secrets-inventory-label-minimization --strict`.
- [ ] 2.2 `python3 scripts/check_spec_overwrites.py` — no unfrozen baseline losses.
- [ ] 2.3 `python3 scripts/check_countable_tasks.py`.
- [ ] 2.4 `make check-guards`.
- [ ] 2.5 `uv run --no-sync pytest tests/api/test_secrets_v2_inventory.py -q --tb=short` — full
      file green with the new test `xfail`ed.
- [ ] 2.6 Independent privacy/API review with zero unresolved threads (`bu-y5uq4` acceptance
      criterion 6).

## 3. Owner gate (blocks all future work)

- [ ] 3.1 Obtain exact owner approval of this spec artifact (`bu-y5uq4` acceptance criterion 7).
      No handler, runtime, or frontend change may land before this step.

## 4. Future bounded implementation (blocked on Task 3, out of this bead)

- [ ] 4.1 Remove `category` / `description` construction from `_content_blind_system` /
      `_content_blind_cli` and the corresponding fields from `SystemSecretSummary` /
      `CliRuntimeSummary` in `src/butlers/api/routers/secrets_v2.py`.
- [ ] 4.2 Remove `xfail(strict=True)` from
      `test_inventory_system_and_cli_rows_omit_description_and_category_but_retain_key` and update
      the superseded positive assertions in
      `test_inventory_system_and_cli_rows_omit_every_probe_and_audit_sentinel`.
- [ ] 4.3 Update `frontend/src/api/types.ts` / `frontend/src/api/client.ts` and any passport
      system/CLI row rendering that reads `description` or `category` off the inventory response.
- [ ] 4.4 File a new bead for this implementation step; do not fold it into `bu-y5uq4`.
