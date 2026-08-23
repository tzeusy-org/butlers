#!/usr/bin/env bash
# Setup worktree with symlinked caches from the main repo.
# This script symlinks large gitignored caches (node_modules) from the main
# repo into the worktree, saving disk space and keeping package.json changes
# in sync across worktrees.
#
# Usage: ./scripts/setup_worktree.sh (run from inside the worktree)

set -euo pipefail

# Detect the main repo root via git's own common-dir resolution.
#
# NOTE: this repo's worktrees live outside the main repo tree (e.g.
# /home/tze/.butlers-worktrees/<name>, per AGENTS.md), not nested inside it,
# so a parent-directory walk from the worktree can never reach the main repo
# root. `git rev-parse --git-common-dir` is the mechanism git (and bd) use
# internally to find the shared .git across all linked worktrees; the main
# repo root is simply its parent directory.
_find_main_repo_root() {
  local worktree_dir git_common_dir
  worktree_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

  if ! git_common_dir="$(git -C "$worktree_dir" rev-parse --git-common-dir 2>/dev/null)"; then
    echo "Error: Could not determine git common dir from $worktree_dir" >&2
    return 1
  fi

  # --git-common-dir may be returned relative to $worktree_dir; resolve to
  # absolute before taking the parent.
  case "$git_common_dir" in
    /*) : ;;
    *) git_common_dir="$worktree_dir/$git_common_dir" ;;
  esac

  (cd "$git_common_dir/.." && pwd)
}

MAIN_REPO_ROOT="$(_find_main_repo_root)"
WORKTREE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$WORKTREE_ROOT" = "$MAIN_REPO_ROOT" ]; then
  echo "Error: scripts/setup_worktree.sh must run from a linked worktree, not the main checkout." >&2
  exit 2
fi

# List of cache directories to symlink (relative paths from repo root).
# Each entry should be a gitignored directory in the main repo.
CACHE_DIRS=(
  "frontend/node_modules"
)

SYMLINKED_COUNT=0

for cache_dir in "${CACHE_DIRS[@]}"; do
  source_path="${MAIN_REPO_ROOT}/${cache_dir}"
  target_path="${WORKTREE_ROOT}/${cache_dir}"

  # Skip if source doesn't exist (main repo hasn't run npm install yet)
  if [ ! -e "$source_path" ]; then
    continue
  fi

  # Create parent directory if needed
  target_parent="$(dirname "$target_path")"
  if [ ! -d "$target_parent" ]; then
    mkdir -p "$target_parent"
  fi

  # Remove existing target (file, symlink, or directory) for idempotency
  rm -rf "$target_path"

  # Create symlink: -s (symlink), -n (don't follow target symlinks)
  ln -sn "$source_path" "$target_path"
  SYMLINKED_COUNT=$((SYMLINKED_COUNT + 1))
done

if [ "$SYMLINKED_COUNT" -eq 0 ]; then
  echo "No caches available to symlink (main repo hasn't run npm install yet)."
else
  echo "Symlinked $SYMLINKED_COUNT cache directory(ies) from main repo."
fi

# Copy the machine-local Dolt server pointer into the new worktree.
#
# .beads/metadata.json is untracked (gitignored, per-machine runtime state)
# and is NOT carried into a fresh `git worktree add` checkout. Without it, bd
# invoked from git hooks (post-checkout/pre-commit/etc., which fire on every
# checkout/commit inside the worktree) can fail to discover the shared Dolt
# server and fall back to a from-scratch import of the multi-MB
# .beads/issues.export.jsonl — an 8+ minute operation that contends the
# shared :3307 Dolt server for every agent working in the fleet (bu-dna8i).
BEADS_METADATA_SOURCE="${MAIN_REPO_ROOT}/.beads/metadata.json"
BEADS_METADATA_TARGET="${WORKTREE_ROOT}/.beads/metadata.json"

if [ -f "$BEADS_METADATA_SOURCE" ]; then
  if [ ! -f "$BEADS_METADATA_TARGET" ]; then
    mkdir -p "${WORKTREE_ROOT}/.beads"
    cp "$BEADS_METADATA_SOURCE" "$BEADS_METADATA_TARGET"
    echo "Copied .beads/metadata.json from main repo."
  fi
else
  echo "Warning: ${BEADS_METADATA_SOURCE} not found; skipping .beads/metadata.json copy." >&2
fi
