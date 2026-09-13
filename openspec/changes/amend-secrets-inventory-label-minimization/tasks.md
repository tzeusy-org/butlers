## 1. Correct the draft artifact (this change)

- [x] 1.1 Rebuild the whole `Secrets Inventory and Per-Credential Read Endpoints` requirement from
      the current baseline, preserving all six scenario headings and every unrelated clause.
- [x] 1.2 Preserve the now-canonical, archived
      `project-secret-read-endpoints-content-blind` detail DTO clauses with per-family precision:
      system `SystemCredentialDetail` publishes `key` / `category` / `description`; CLI
      `CliCredentialDetail` publishes `id` / `label` and fixed capability categories, not fields
      named `key` / `category` / `description` or raw scopes. The delta was rebuilt against the
      refreshed baseline after the sibling archived, without retaining obsolete payload clauses.
- [x] 1.3 Apply only the inventory proposal: `SystemSecretSummary` and `CliRuntimeSummary` retain
      raw `key` while omitting `category` and `description` as absent fields, including in degraded
      responses. Name this partial metadata minimization rather than anonymity.
- [x] 1.4 Align proposal, design, tasks, and delta spec on the drafting-only authority boundary;
      leave runtime, frontend, tests, adoption, and archive state unchanged.

## 2. Validate the corrected draft

- [x] 2.1 `openspec validate amend-secrets-inventory-label-minimization --strict`.
- [x] 2.2 Same-requirement scan and body comparison against the refreshed baseline and archived
      `project-secret-read-endpoints-content-blind`, confirming collateral preservation and the
      inventory-only proposal.
- [x] 2.3 `make check-spec-overwrites` without a ratchet rebaseline.
- [x] 2.4 `make check-guards`.
- [x] 2.5 `git diff --check`.
- [ ] 2.6 Open a PR for independent privacy/API exact-head review, recording the corrected commit
      and artifact digest; resolve every finding before exact owner adoption.

Existing API tests are evidence only and remain unchanged: detail coverage lives in
`tests/api/test_secrets_v2_per_credential.py`; the strict-xfail inventory target and the current
positive content-blind inventory test remain in `tests/api/test_secrets_v2_inventory.py`.

## 3. Owner gate (blocks implementation)

- [ ] 3.1 Obtain owner approval of the exact corrected artifact recorded by the independent review.
      Draft completion, PR merge, or canonical adoption of the separate detail DTO contract does
      not satisfy this gate.

## 4. Future bounded implementation (`bu-qj0ekp`, out of this bead)

- [ ] 4.1 After exact owner adoption, remove `category` / `description` from
      `SystemSecretSummary`, `CliRuntimeSummary`, `_content_blind_system`, and
      `_content_blind_cli` only; preserve raw `key` and every unrelated inventory field.
- [ ] 4.2 Remove `xfail(strict=True)` from
      `test_inventory_system_and_cli_rows_omit_description_and_category_but_retain_key` and update
      the superseded positive field assertions in
      `test_inventory_system_and_cli_rows_omit_every_probe_and_audit_sentinel` in the same change.
- [ ] 4.3 Update frontend types and rendering only where they consume the omitted inventory fields.
- [ ] 4.4 Before future archive/adoption, rebuild against the then-current baseline and rerun the
      body-loss guard; do not use `--update-baseline` to conceal a clause loss.
