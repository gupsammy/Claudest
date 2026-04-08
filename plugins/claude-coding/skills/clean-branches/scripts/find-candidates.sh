#!/usr/bin/env bash
# find-candidates.sh — Find merged and stale git branches
# Usage: find-candidates.sh [pattern]
# Output: Two labeled sections (MERGED / STALE), one branch per line.
#         Merged branches checked out in a worktree are annotated: branch [worktree:/path]
#         Empty section = no candidates of that type.
# Exit 0 always; downstream decides what to do with empty output.

set -euo pipefail

PATTERN="${1:-}"

# Detect main branch name (prefer main, fall back to master)
if git rev-parse --verify main >/dev/null 2>&1; then
  BASE="main"
elif git rev-parse --verify master >/dev/null 2>&1; then
  BASE="master"
else
  echo "ERROR: no main or master branch found" >&2
  exit 1
fi

# Build worktree map: branch -> worktree path
# `git worktree list --porcelain` emits blocks like:
#   worktree /path
#   HEAD <sha>
#   branch refs/heads/<name>   (or "detached" for detached HEAD)
declare -A WORKTREE_MAP
current_wt=""
while IFS= read -r line; do
  if [[ "$line" == worktree\ * ]]; then
    current_wt="${line#worktree }"
  elif [[ "$line" == branch\ refs/heads/* ]]; then
    branch_name="${line#branch refs/heads/}"
    WORKTREE_MAP["$branch_name"]="$current_wt"
  fi
done < <(git worktree list --porcelain)

# --- Merged branches ---
echo "=== MERGED ==="
MERGED=$(git branch --merged "$BASE" 2>/dev/null | grep -v "^\*" | sed 's/^[+ ]*//' | grep -vE '^(main|master|develop)$' || true)
if [ -n "$PATTERN" ]; then
  MERGED=$(echo "$MERGED" | grep "$PATTERN" || true)
fi
while IFS= read -r branch; do
  [ -z "$branch" ] && continue
  if [[ -n "${WORKTREE_MAP[$branch]+_}" ]]; then
    echo "$branch [worktree:${WORKTREE_MAP[$branch]}]"
  else
    echo "$branch"
  fi
done <<< "$MERGED"

# --- Stale branches (no commits in 30+ days) ---
# Unix timestamps used for accurate threshold — git relative dates miss edge cases
echo "=== STALE ==="
CUTOFF=$(python3 -c "import time; print(int(time.time()) - 30*86400)")
while read -r branch ts reldate; do
  # Skip protected branches
  case "$branch" in main|master|develop|release/*) continue ;; esac
  # Apply pattern filter if provided
  if [ -n "$PATTERN" ] && [[ "$branch" != $PATTERN ]]; then
    continue
  fi
  if (( ts < CUTOFF )); then
    if [[ -n "${WORKTREE_MAP[$branch]+_}" ]]; then
      echo "$branch ($reldate) [worktree:${WORKTREE_MAP[$branch]}]"
    else
      echo "$branch ($reldate)"
    fi
  fi
done < <(git for-each-ref --sort=-committerdate \
  --format='%(refname:short) %(committerdate:unix) %(committerdate:relative)' \
  refs/heads/)
