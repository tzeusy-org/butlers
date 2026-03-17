# Healing Worktree

## Purpose

Git worktree lifecycle management for healing agents. Each healing attempt runs in an isolated worktree branched from `main`, ensuring healing agents cannot corrupt the daemon's working tree. Includes automatic cleanup of stale worktrees and robust error handling for git operation failures.

## ADDED Requirements

### Requirement: Worktree Creation
The system SHALL create a dedicated git worktree for each healing attempt. The worktree is branched from the current `main` HEAD.

#### Scenario: Worktree created for new healing attempt
- **WHEN** the dispatcher spawns a healing attempt with fingerprint `abc123def456...` for butler `email`
- **THEN** a new branch `hotfix/email/abc123def456-<epoch>` is created from `main` HEAD
- **AND** a worktree is added at `<repo-root>/.healing-worktrees/hotfix/email/abc123def456-<epoch>/`

#### Scenario: Branch name format
- **WHEN** a worktree is created for fingerprint `abc123def456789...` at epoch `1710700000`
- **THEN** the branch name uses the first 12 hex chars of the fingerprint: `hotfix/email/abc123def456-1710700000`

#### Scenario: .healing-worktrees in .gitignore
- **WHEN** the `.gitignore` is checked
- **THEN** `.healing-worktrees/` is listed to prevent accidental commits of worktree contents

### Requirement: Worktree Creation Error Handling
If worktree creation fails at any stage, the system SHALL clean up partial state and report the failure to the caller.

#### Scenario: git worktree add fails (disk full, permission denied)
- **WHEN** `git worktree add` fails with a non-zero exit code
- **THEN** the orphaned branch (if created) is deleted via `git branch -D`
- **AND** the function raises a `WorktreeCreationError` with the git stderr output

#### Scenario: Branch creation fails (already exists)
- **WHEN** `git branch <name> main` fails because the branch name already exists
- **THEN** no worktree is created
- **AND** the function raises a `WorktreeCreationError` indicating the branch collision

#### Scenario: Git lock file blocks operation
- **WHEN** `git worktree add` fails because another git operation holds the lock
- **THEN** the function raises a `WorktreeCreationError` (no retry — the dispatcher handles the failure)

#### Scenario: Dispatcher handles worktree creation failure
- **WHEN** `create_healing_worktree()` raises `WorktreeCreationError`
- **THEN** the dispatcher transitions the healing attempt to `failed` with `error_detail` containing the git error message
- **AND** no healing agent is spawned

### Requirement: Worktree Isolation
The healing agent's CWD SHALL be set to the worktree path. The agent SHALL NOT have write access to the main working tree.

#### Scenario: Agent runs in worktree
- **WHEN** a healing agent session is spawned
- **THEN** the runtime adapter's working directory is the worktree path, not the main repo checkout

#### Scenario: Main repo unaffected
- **WHEN** a healing agent creates commits in its worktree
- **THEN** the main repo's `HEAD`, index, and working tree are unchanged

#### Scenario: Worktree shares git objects
- **WHEN** a worktree is created
- **THEN** it shares `.git/objects` with the main repo (no full clone — lightweight)
- **AND** commits created in the worktree are visible from the main repo's `git log --all`

### Requirement: Worktree Cleanup on Completion
The system SHALL remove the worktree and conditionally delete the local branch after the healing attempt reaches a terminal state.

#### Scenario: Cleanup after PR creation
- **WHEN** a healing attempt transitions to `pr_open`
- **THEN** the worktree is removed via `git worktree remove`
- **AND** the local branch is NOT deleted (it backs the open PR on the remote)

#### Scenario: Cleanup after failure
- **WHEN** a healing attempt transitions to `failed`, `unfixable`, or `timeout`
- **THEN** the worktree is removed via `git worktree remove`
- **AND** the local branch is deleted via `git branch -D`

#### Scenario: Cleanup after anonymization failure
- **WHEN** a healing attempt transitions to `anonymization_failed`
- **THEN** the worktree is removed via `git worktree remove`
- **AND** the remote branch is deleted via `git push origin --delete <branch>` (it was pushed before PR creation was attempted)
- **AND** the local branch is deleted via `git branch -D`

#### Scenario: Cleanup failure is non-fatal
- **WHEN** `git worktree remove` fails (e.g. worktree already removed, directory doesn't exist)
- **THEN** the error is logged at WARNING level and the healing attempt status transition is unchanged
- **AND** the stale worktree reaper will clean it up on next daemon startup

#### Scenario: Force-remove for dirty worktrees
- **WHEN** `git worktree remove` fails because the worktree has uncommitted changes
- **THEN** `git worktree remove --force` is used as a fallback

### Requirement: Stale Worktree Reaper
On dispatcher startup (daemon boot, after recovery), the system SHALL scan `.healing-worktrees/` and clean up worktrees that are no longer needed.

#### Scenario: Stale worktree with terminal attempt cleaned
- **WHEN** the daemon starts and `.healing-worktrees/hotfix/email/abc123-1710600000/` exists
- **AND** the corresponding healing attempt has status `failed` and `closed_at` is 36 hours ago
- **THEN** the worktree and branch are removed

#### Scenario: Active worktree preserved on startup
- **WHEN** the daemon starts and a worktree exists for a healing attempt with status `investigating`
- **AND** the attempt's `updated_at` is within the timeout window
- **THEN** the worktree is NOT removed

#### Scenario: Orphaned worktree with no matching attempt
- **WHEN** the daemon starts and a worktree directory exists in `.healing-worktrees/`
- **AND** no `healing_attempts` row matches the branch name
- **THEN** the worktree and branch are removed (orphan from a crash before DB insertion)
- **AND** a WARNING is logged: "Removing orphaned healing worktree with no matching attempt: {branch}"

#### Scenario: Orphaned branch with no worktree
- **WHEN** the daemon starts and a local branch matching `hotfix/*/` exists
- **AND** no worktree exists for it and no `healing_attempts` row with status `investigating` or `pr_open` references it
- **THEN** the branch is deleted via `git branch -D`

### Requirement: Worktree Function Signatures
The system SHALL expose:
- `create_healing_worktree(repo_root: Path, butler_name: str, fingerprint: str) -> tuple[Path, str]` returning `(worktree_path, branch_name)`. Raises `WorktreeCreationError` on failure.
- `remove_healing_worktree(repo_root: Path, branch_name: str, delete_branch: bool = True, delete_remote: bool = False) -> None`. Best-effort, logs warnings on failure.
- `reap_stale_worktrees(repo_root: Path, pool: asyncpg.Pool) -> int` returning count of reaped worktrees (includes orphans).

#### Scenario: Create returns path and branch
- **WHEN** `create_healing_worktree(repo, "email", "abc123...")` is called
- **THEN** it returns `(Path(".healing-worktrees/hotfix/email/abc123def456-<epoch>"), "hotfix/email/abc123def456-<epoch>")`

#### Scenario: Reaper returns count
- **WHEN** `reap_stale_worktrees(repo, pool)` finds and removes 3 stale worktrees (2 terminal + 1 orphan)
- **THEN** it returns `3`

#### Scenario: Remove with remote cleanup
- **WHEN** `remove_healing_worktree(repo, branch, delete_branch=True, delete_remote=True)` is called
- **THEN** the worktree is removed, the local branch is deleted, and `git push origin --delete <branch>` is called
