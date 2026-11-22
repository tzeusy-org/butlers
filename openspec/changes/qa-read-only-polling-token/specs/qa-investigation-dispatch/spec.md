## MODIFIED Requirements

### Requirement: Investigation Agent Sandbox
Investigation agents SHALL operate in a sandboxed environment without GitHub publication credentials and with no access to publisher or butler runtime secrets.

ID: REQ-qa-investigation-dispatch-001
Source: owner-adopted QA publication contract 90d904ee0e4cb9a8c2b23ca07739bf580d167fa9e2f1d7f0a5b0a59959b2d8e9; RFC0015
Scope: v1-mandatory

#### Scenario: Agent environment
- **WHEN** the investigation agent is spawned in a worktree
- **THEN** only the trusted deterministic publisher may resolve `BUTLERS_QA_GH_TOKEN`; investigation agents receive no GitHub credential or generic publication capability
- **AND** if configured, the QA staffer resolves `BUTLERS_QA_GIT_AUTHOR_NAME` and `BUTLERS_QA_GIT_AUTHOR_EMAIL` and injects them as `GIT_AUTHOR_*` / `GIT_COMMITTER_*` so non-interactive `git commit` does not depend on per-worktree `git config`
- **AND** the agent's environment contains only: `PATH`, and build-tool variables (`UV_CACHE_DIR`, etc.)
- **AND** it does NOT have: butler DB connection strings, API keys, OAuth tokens, user data, or any `BUTLERS_*` env vars
- **AND** it does NOT have MCP server connections (the spawner automatically sets empty MCP server config when `trigger_source="qa"`, preventing access to live production state and suppressing the Codex adapter's MCP-discovery retry path)
- **AND** its filesystem scope is the worktree directory only

#### Scenario: Agent runs from a QA helper workspace
- **WHEN** the investigation agent is spawned in a worktree
- **THEN** its current working directory is a QA-owned helper subdirectory inside the worktree, not the repository root
- **AND** that helper directory contains a local `AGENTS.md` override that disables unrelated repo-level workflow instructions such as `bd` usage, generic session-close rules, or self-managed PR/push steps
- **AND** the helper directory exposes the repo roots needed for normal QA commands (`src/`, `tests/`, `roster/`, `frontend/`, `pyproject.toml`, `uv.lock`) so repo-relative validation commands still work unchanged

#### Scenario: Publication unavailable without an isolated publisher
- **WHEN** an investigation agent succeeds with commits, or a follow-up agent finishes on an open PR
- **THEN** no `git push` and no `gh pr create` runs, and there is no fallback that restores token injection
- **AND** the investigation transitions to `failed` with reason `publication_unavailable`; a follow-up records the same reason in `last_follow_up_error` and the PR is left unchanged
- **AND** the dispatcher keeps only a read-only token (`BUTLERS_QA_GH_READ_TOKEN`) for PR-status polling and its own fetch/worktree preparation; a missing read token leaves PR status `unknown`, never `closed`
- **AND** investigator containment is not enforced while investigators share the daemon's pod as root, so the daemon never resolves a write-scoped GitHub credential: a leftover `BUTLERS_QA_GH_TOKEN` key is detected by presence only, refused, and reported as an action-needed condition once per patrol

#### Scenario: Dispatcher git subprocesses in the shared clone are isolated
- **WHEN** the dispatcher runs git against the shared clone or an agent worktree (main refresh, follow-up fetch, worktree create/remove, managed-clone refresh, commit diff snapshot, no-op branch detection)
- **THEN** the subprocess runs with an allowlisted environment that carries no butler runtime secret, and command-line git config that neutralises hooks, fsmonitor, credential helpers, `core.sshCommand`, `core.askPass` and the `ext::` and `git://` transports
- **AND** named-remote fetches pin `--upload-pack=git-upload-pack` and the commit diff snapshot passes `--no-ext-diff --no-textconv` and no-op branch detection passes `--no-show-signature`, so agent-planted `.git/config` commands and drivers never execute

#### Scenario: GitHub credentials from secrets store
- **WHEN** the QA staffer needs GitHub access today
- **THEN** it retrieves only the read-only polling token at key `BUTLERS_QA_GH_READ_TOKEN` (managed via the dashboard at /secrets); the write-scoped key is never resolved
- **AND** the dedicated one-repository credential uses minimum documented provider grants; the trusted publisher confines effective operations to its attempt-bound branch publication, PR create/update and fixed sanitized labels
- **AND** the agent and publisher action surface SHALL NOT merge, approve, review or queue; humans remain in the merge seat. Coarse provider grants may include greater permission, and trusted-publisher compromise remains an explicit residual risk, not provider-side absence of permission
- **AND** if the read-only secret is not found, polling is skipped, PR status stays `unknown` and one action-needed alert is raised per patrol
- **AND** if GitHub authenticates the token but denies repository write authorization (`Permission to … denied` or HTTP 403), the investigation transitions to `failed` with the stable `git_auth_failed` class and content-blind guidance to verify repository scope and organization authorization

