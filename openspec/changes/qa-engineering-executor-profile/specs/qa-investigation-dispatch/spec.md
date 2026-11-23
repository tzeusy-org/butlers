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

#### Scenario: Executor prompt excludes butler persona and context
- **WHEN** a session is launched with trigger source `qa`
- **THEN** its system prompt is the core-owned engineering-executor profile only, identical whichever butler's spawner launches it
- **AND** it carries no butler persona, DB prompt override, memory context, situational or timezone preamble, or blind-spot disclosure, and no memory is recalled or stored for the session
- **AND** the session's prompt provenance is the single source `engineering_executor_profile@v1`
- **AND** if the profile cannot be read the launch is refused (`executor_profile_unavailable`) rather than falling back to the persona prompt

#### Scenario: Agent runs from a QA helper workspace
- **WHEN** the investigation agent is spawned in a worktree
- **THEN** its current working directory is a QA-owned helper subdirectory inside the worktree, not the repository root
- **AND** that helper directory contains a local `AGENTS.md` override that disables unrelated repo-level workflow instructions such as `bd` usage, generic session-close rules, or self-managed PR/push steps
- **AND** the helper directory exposes the repo roots needed for normal QA commands (`src/`, `tests/`, `roster/`, `frontend/`, `pyproject.toml`, `uv.lock`) so repo-relative validation commands still work unchanged

#### Scenario: Publication unavailable without an isolated publisher
- **WHEN** an investigation agent succeeds with commits, or a follow-up agent finishes on an open PR
- **THEN** no `git push` and no `gh pr create` runs, and there is no fallback that restores token injection
- **AND** the investigation transitions to `failed` with reason `publication_unavailable`; a follow-up records the same reason in `last_follow_up_error` and the PR is left unchanged
- **AND** the dispatcher keeps the token only for read-only PR-status polling and its own fetch/worktree preparation

#### Scenario: Dispatcher git subprocesses in the shared clone are isolated
- **WHEN** the dispatcher runs git against the shared clone or an agent worktree (main refresh, follow-up fetch, worktree create/remove, managed-clone refresh, commit diff snapshot, no-op branch detection)
- **THEN** the subprocess runs with an allowlisted environment that carries no butler runtime secret, and command-line git config that neutralises hooks, fsmonitor, credential helpers, `core.sshCommand`, `core.askPass` and the `ext::` and `git://` transports
- **AND** named-remote fetches pin `--upload-pack=git-upload-pack` and the commit diff snapshot passes `--no-ext-diff --no-textconv` and no-op branch detection passes `--no-show-signature`, so agent-planted `.git/config` commands and drivers never execute

#### Scenario: GitHub credentials from secrets store
- **WHEN** the QA staffer needs to create a PR
- **THEN** it retrieves the GitHub token from the system secrets store at key `BUTLERS_QA_GH_TOKEN` (managed via the dashboard at /secrets)
- **AND** the dedicated one-repository credential uses minimum documented provider grants; the trusted publisher confines effective operations to its attempt-bound branch publication, PR create/update and fixed sanitized labels
- **AND** the agent and publisher action surface SHALL NOT merge, approve, review or queue; humans remain in the merge seat. Coarse provider grants may include greater permission, and trusted-publisher compromise remains an explicit residual risk, not provider-side absence of permission
- **AND** if the secret is not found, the investigation completes but transitions to `failed` with reason `"no_gh_token"`
- **AND** if GitHub authenticates the token but denies repository write authorization (`Permission to … denied` or HTTP 403), the investigation transitions to `failed` with the stable `git_auth_failed` class and content-blind guidance to verify repository scope and organization authorization

### Requirement: QA Investigation Agent Prompt
The QA investigation agent SHALL receive a prompt that includes the error context from the discovery source, not from a live session. No raw logs or user data are included.

#### Scenario: Agent prompt composition
- **WHEN** the investigation agent is spawned
- **THEN** its prompt includes: error fingerprint, exception type, sanitized event summary, call site (module path), source butler name, occurrence count and time range, discovery source type, and instructions to: read relevant source code, identify root cause, implement a fix, run targeted tests, commit with a descriptive message
- **AND** the prompt explicitly instructs the agent to NOT include any user data, PII, or sensitive content in commits or PR descriptions
- **AND** the prompt explicitly instructs the agent to ignore unrelated repository workflow instructions and to not run `bd`, push branches manually, or open PRs itself

#### Scenario: Agent uses the exact workhorse rung
- **WHEN** the investigation agent is spawned
- **THEN** its dispatch intent requires a workspace-capable runtime (`workspace_agent`) at `reversible` consequence and resolves the exact `workhorse` rung with no tier fallthrough to `cheap`, `specialty` or `local`
- **AND** a runtime that cannot run shell and filesystem tools in the worktree (the direct API adapter) is excluded as unfit, never ranked
- **AND** if no entry at the rung fits, the investigation is skipped with dispatch decision `no_model`, which is not counted toward the circuit breaker, and no cheaper model is substituted
- **AND** admission resolves under the same butler identity, rung and intent as the launch, so it cannot admit a model the launch would refuse
- **AND** a provider authentication failure inside the executor sandbox is recorded as `executor_env_unfit` and does not count toward the catalog-entry circuit breaker

#### Scenario: Agent context from reactive butler reports
- **WHEN** the finding originated from a `butler_reports` source (via `report_error`) with a non-empty `context` field
- **THEN** the agent's diagnostic reasoning is included in the investigation prompt (after anonymization)
- **AND** this gives the investigation agent a head start on diagnosis
