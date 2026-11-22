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
- **AND** a follow-up records `publication_unavailable` in `last_follow_up_error` and the PR is left unchanged
- **AND** the dispatcher keeps the token only for read-only PR-status polling and its own fetch/worktree preparation
- **AND** a committed investigation is sealed and retained as an unpublished proposal (next scenario) rather than discarded; its attempt still ends `failed` with reason `publication_unavailable`, and no new attempt status exists

#### Scenario: A committed investigation is kept as an unpublished proposal
- **WHEN** an investigation succeeds with commits and no publisher is injected
- **THEN** the dispatcher seals the exact `base..head` commits with the same validator the publisher uses and retains the sealed export, with no credential, push or PR
- **AND** the phase session completes, and the case dossier reports `proposal_state = unpublished` with the retained diff and notes
- **AND** the attempt carrying a retained export and the `publication_unavailable` reason counts as an execution success for the circuit breaker: it breaks a failure streak and is never counted as a failure, both in dispatch admission and in `GET /api/qa/circuit-breaker`, which share one definition
- **AND** a validator refusal (anonymization residue, unsafe objects, unbound base or identity) records the publisher's refusal receipt, ends the attempt as the publisher path does today, and counts as an execution failure
- **AND** an export that cannot be retained for this attempt (retention store error, or an existing export row on a re-run: `export_unretained`, `export_changed`, `export_pruned`) ends the attempt `failed` with reason `publication_unretained`, which counts as an execution failure and holds nothing
- **AND** timeouts, agent failures and other genuine execution failures keep counting, so five in a row still trip the breaker

#### Scenario: A held proposal suppresses re-investigation while publication is absent
- **WHEN** no publisher is injected and a novel finding has a fingerprint whose latest unpublished proposal closed within the hold window (7 days, never more than the 30-day export retention) and whose retained export is not pruned
- **THEN** the dispatcher records a decision-only `publication_paused` dispatch event and `dedup_reason = publication_paused` before calling `create_or_join_attempt`
- **AND** no `healing_attempts` row, worktree, runtime session or LLM invocation exists as a consequence
- **AND** the gate is inert whenever a publisher is injected, and a pruned export or an expired window makes the fingerprint eligible again for exactly one re-investigation
- **AND** nothing publishes a retained proposal automatically today, because no publisher exists; flagging exports as sealed before isolation is proven, so each needs owner approval per pull request, is deferred to a bu-ejow5p.11 (per-attempt credential-free investigator with an executable isolation verifier)

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
