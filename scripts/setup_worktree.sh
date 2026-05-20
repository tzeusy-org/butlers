#!/usr/bin/env bash
# Setup worktree with symlinked caches from the main repo.
# This script symlinks large gitignored caches (node_modules) from the main
# repo into the worktree, saving disk space and keeping package.json changes
# in sync across worktrees.
#
# Usage: ./scripts/setup_worktree.sh (run from inside the worktree)

set -euo pipefail

# Detect the main repo root by walking up from the worktree until we find
# a directory whose .git is a directory (not a file).
_find_main_repo_root() {
  local current_dir
  current_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

  while [ "$current_dir" != "/" ]; do
    if [ -d "$current_dir/.git" ] && [ ! -f "$current_dir/.git" ]; then
      echo "$current_dir"
      return 0
    fi
    current_dir="$(dirname "$current_dir")"
  done

  echo "Error: Could not find main repo root" >&2
  return 1
}

MAIN_REPO_ROOT="$(_find_main_repo_root)"
WORKTREE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

  # Use ln -sfn for idempotency: -s (symlink), -f (force overwrite), -n (treat target as file)
  ln -sfn "$source_path" "$target_path"
  SYMLINKED_COUNT=$((SYMLINKED_COUNT + 1))
done

if [ "$SYMLINKED_COUNT" -eq 0 ]; then
  echo "No caches available to symlink (main repo hasn't run npm install yet)."
  exit 0
fi

echo "Symlinked $SYMLINKED_COUNT cache directory(ies) from main repo."
