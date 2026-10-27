## MODIFIED Requirements

### Requirement: Permissions and Security Model
The QA Staffer SHALL operate with a least-privilege security model: dedicated trusted-publisher credentials, a closed no-merge/no-review publication surface, sandboxed investigation environments, and no access to butler runtime secrets.

ID: REQ-staffer-qa-010
Source: owner-adopted QA publication contract 90d904ee0e4cb9a8c2b23ca07739bf580d167fa9e2f1d7f0a5b0a59959b2d8e9; RFC0015
Scope: v1-mandatory

#### Scenario: Dedicated GitHub credentials via Tier 1 system secrets
- **WHEN** the QA Staffer creates PRs
- **THEN** it retrieves the GitHub token via `CredentialStore.resolve("BUTLERS_QA_GH_TOKEN")` only for the dedicated QA credential category; no owner-token or ambient-token fallback is permitted
- **AND** the secret is provisioned in the QA staffer's own butler_secrets table with `category = "qa"`, `is_sensitive = true` — managed via the QA staffer's secrets page on the dashboard at /secrets
- **AND** only the trusted deterministic publisher resolves the dedicated one-repository credential with minimum documented provider grants; investigators receive no GitHub credential or publication capability
- **AND** effective publisher operations SHALL exclude merge/approval/review/queue; humans remain in the merge seat. Coarse provider grants may be broader than the closed publisher surface; compromise of that trusted component remains a residual risk
- **AND** if the secret is not found at any tier, the investigation completes but transitions to `failed` with reason `"no_gh_token"`
- **AND** authentication or repository-authorization rejection transitions to `failed` with reason class `git_auth_failed` and content-blind remediation guidance; it does not claim the token is absent or unconfigured when GitHub authenticated an identity

#### Scenario: Investigation agent sandbox
- **WHEN** an investigation agent is spawned in a worktree
- **THEN** its environment contains only: `PATH`, and build-tool variables (`UV_CACHE_DIR`, etc.)
- **AND** it does NOT have access to: butler DB connection strings, API keys, OAuth tokens, user data, or any `BUTLERS_*` env vars
- **AND** it does NOT have MCP server connections (the spawner automatically sets empty MCP server config when `trigger_source="qa"`, preventing access to live production state and suppressing the Codex adapter's MCP-discovery retry path)
- **AND** its filesystem access is limited to the worktree directory

#### Scenario: Log scanner reads are local-only
- **WHEN** the log scanner reads log files
- **THEN** log content is processed in-memory and never transmitted to external services
- **AND** only computed fingerprints, exception types, call sites, and sanitized event summaries are persisted to the DB
- **AND** raw log lines are not stored in `qa_findings`

#### Scenario: Cross-butler DB access via sanctioned SQL view
- **WHEN** the session record source queries for failed sessions
- **THEN** it queries ONLY the sanctioned `public.v_qa_recent_failures` SQL view (RFC 0010 pattern)
- **AND** it does NOT query butler-owned schemas directly
- **AND** the view is read-only, date-filtered, and created via auditable migration with explicit GRANT
- **AND** it writes findings only to `public.qa_patrols`, `public.qa_findings`, and `public.healing_attempts`
