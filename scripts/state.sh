#!/usr/bin/env bash
set -euo pipefail
# Checkout has already supplied a credential helper; don't embed tokens in URLs.
root="$(pwd)"
mkdir -p state
if git ls-remote --exit-code --heads origin parser-state >/dev/null; then
  git fetch origin parser-state
  git worktree add state -B parser-state FETCH_HEAD
else
  result=$?
  if [ "$result" -ne 2 ]; then exit "$result"; fi
  git worktree add --detach state HEAD
  git -C state checkout --orphan parser-state
  git -C state rm -rf .
  git -C state commit --allow-empty -m 'Initialize persistent parser state'
  git -C state push origin HEAD:parser-state
fi
printf 'STATE_GIT_DIR=%s/state\n' "$root" >> "$GITHUB_ENV"
