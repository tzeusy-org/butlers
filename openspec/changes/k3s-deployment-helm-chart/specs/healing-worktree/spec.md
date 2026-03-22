## ADDED Requirements

### Requirement: Graceful disable via configuration

The healing worktree subsystem SHALL be disableable via `healing.enabled = false` in `butler.toml` or `BUTLERS_HEALING_ENABLED=false` env var. When disabled, `create_healing_worktree()` SHALL return a sentinel error without attempting any git operations. `reap_stale_worktrees()` SHALL be a no-op.

#### Scenario: Healing disabled skips worktree creation
- **WHEN** `create_healing_worktree()` is called and `healing.enabled = false`
- **THEN** the function returns an error result indicating healing is disabled
- **AND** no `git worktree` or `git branch` commands are executed

#### Scenario: Healing disabled skips startup reaping
- **WHEN** `reap_stale_worktrees()` is called and `healing.enabled = false`
- **THEN** the function returns immediately without scanning the filesystem

#### Scenario: Healing disabled does not require git repo
- **WHEN** the process runs in a container without a `.git` directory and `healing.enabled = false`
- **THEN** no errors are raised related to missing git repository
