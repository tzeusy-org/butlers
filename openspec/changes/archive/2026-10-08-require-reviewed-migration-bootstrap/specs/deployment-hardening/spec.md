## MODIFIED Requirements

### Requirement: Strict DB-Role Enforcement Under Hardened Posture

Under the hardened posture, DB role-enforcement and permission-gate failures SHALL fail closed rather than silently downgrade privileges.
- A missing or unverifiable PostgreSQL runtime role SHALL cause startup (or the affected connection acquire) to fail loudly instead of proceeding with the connecting user's privileges. Under `dev`, the existing graceful fallback SHALL be retained but the degraded state SHALL be reported by the degraded-safety indicator.
- This adds an opt-in strict mode bound to the hardened posture; it does not change the `database-security` graceful-fallback policy for `dev`. Under `dev`, `src/butlers/db.py` retains fail-open behavior (logs "Could not verify role … SET ROLE enforcement disabled" or "Role … not found; SET ROLE enforcement disabled" and proceeds with the connecting user's privileges). Under the hardened posture, `Database.strict_role_enforcement` (the `Database` class in `src/butlers/db.py`) is enabled (auto-detected from `is_hardened_posture()`) and the daemon fails closed rather than silently losing schema isolation for an always-on deployment.
- The foregoing dev continuation and unchanged fallback policy apply to direct runtime connection setup; they do not waive REQ-database-security-011 for supported online migration/startup requests. No online request self-bootstraps or acquires recovery authority.

ID: REQ-deployment-hardening-010
Source: openspec/specs/deployment-hardening/spec.md (Strict DB-Role Enforcement Under Hardened Posture); bu-kqnum.8.10 owner decision and clarification (2026-08-15); REQ-database-security-011
Scope: v1-mandatory

#### Scenario: Missing role fails closed under hardened posture
- **WHEN** the deployment runs under the hardened posture and a butler's runtime
  role cannot be verified or does not exist
- **THEN** the daemon fails loudly (startup or connection acquire) instead of
  silently disabling `SET ROLE` enforcement

#### Scenario: Graceful fallback retained in dev but surfaced
- **WHEN** the deployment runs under `dev` and a runtime role cannot be verified
- **AND** the request is direct runtime connection setup and requests no online migration
- **THEN** the existing graceful fallback applies (enforcement disabled, daemon
  continues) AND the degraded-safety indicator reports that role enforcement is
  disabled
- **AND** supported online migration/startup requests independently refuse a missing reviewed bootstrap prerequisite
