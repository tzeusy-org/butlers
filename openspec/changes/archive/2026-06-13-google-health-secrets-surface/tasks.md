# Tasks — google-health-secrets-surface

This is a **spec-only change**. No implementation code lands here. The tasks cover OpenSpec validation, owner review, and archiving. Implementation is owned by downstream beads (see §Downstream Implementation).

## 1. Spec Validation

- [ ] 1.1 Run `openspec validate google-health-secrets-surface --strict` and confirm zero errors/warnings. Paste verbatim output in PR description.
- [ ] 1.2 Verify `specs/dashboard-google-accounts/spec.md` uses `####` (4 hashtags) for all `Scenario:` headers and `###` for all `Requirement:` headers — per OpenSpec format requirement.
- [ ] 1.3 Verify `specs/butler-secrets/spec.md` MODIFIED requirement block contains the COMPLETE requirement text (not just the delta) — per OpenSpec MODIFIED convention.
- [ ] 1.4 Verify every Scenario uses `SHALL` / `MUST` normative language (not `should` / `may`).
- [ ] 1.5 Confirm `## Source References` section is present in both spec files per `openspec/config.yaml:9-15`.

## 2. Owner Review

- [ ] 2.1 Open PR `agent/bu-9i5uo → main` and request owner review. PR title: `spec: bind Google Health scope surface to /secrets passport + leak-prevention [bu-9i5uo]`.
- [ ] 2.2 Owner verifies leak-prevention invariant (Scenario: multi-account leak prevention in `dashboard-google-accounts`) correctly captures the security requirement.
- [ ] 2.3 Owner verifies route-binding statement (Google scope-set picker + Health card at `/secrets?focus=u:google`) does not conflict with any active redesign work.
- [ ] 2.4 Owner verifies cross-link to `add-connector-oauth-scope-surface` is sufficient (this change does NOT re-spec the `auth_status` enum).
- [ ] 2.5 Owner approves PR. Merge to `main`.

## 3. Archive

- [ ] 3.1 Run `openspec archive google-health-secrets-surface` to merge spec deltas into `openspec/specs/dashboard-google-accounts/spec.md` and produce the delta for `redesign-secrets-passport`'s `butler-secrets` spec.
- [ ] 3.2 Note: `butler-secrets` currently lives in `openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md` (not yet promoted to `openspec/specs/`). The archive process must reconcile accordingly. If `redesign-secrets-passport` has already archived by this point, apply the delta directly to `openspec/specs/butler-secrets/spec.md`.
- [ ] 3.3 Verify `openspec/specs/dashboard-google-accounts/spec.md` now contains the route-binding requirement and multi-account leak-prevention scenario.
- [ ] 3.3a During archive, amend the base spec's "Google Accounts settings page" wording in `§Per-Account Scope Set Picker` to "the `/secrets` passport at `/secrets?focus=u:google` (`PageGoogleAccounts`)" — eliminating the settings-page contradiction introduced by this route-binding requirement.
- [ ] 3.4 Run `openspec validate --specs --strict` across all specs to confirm no regressions from the archive merge.

## 4. Downstream Implementation Unblocking

- [ ] 4.1 Confirm bead `bu-2kejb` (Backend: owner-default secrets inventory surfaces the PRIMARY Google account) is unblocked after this spec is approved. Link it to this change's archived spec section.
- [ ] 4.2 Confirm the frontend bead for `PageGoogleAccounts` scope-set picker (within epic `bu-lmrzg`) can now reference the normative route-binding requirement in `dashboard-google-accounts/spec.md`.
- [ ] 4.3 File a follow-up bead (if not already tracked) for `add-connector-oauth-scope-surface` harmonization: once that change archives its `auth_status` enum, `PageGoogleAccounts` rendering must be verified against the enum contract.
