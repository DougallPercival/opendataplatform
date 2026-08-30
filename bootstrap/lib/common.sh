#!/usr/bin/env bash
# Shared helpers for the bootstrap scripts. Sourced, not executed directly.

set -euo pipefail

# k3s installs its kubectl symlink at /usr/local/bin/kubectl. Some sudo
# configs (a trimmed secure_path, common on hardened RHEL-family systems)
# don't include /usr/local/bin even once the file's right there — which
# silently breaks every bare kubectl/k3s call in scripts that source this
# file, regardless of how they were invoked. Make sure it's findable no
# matter what, once, here, rather than in every script separately.
export PATH="/usr/local/bin:${PATH}"

_c_red=$'\033[0;31m'; _c_yellow=$'\033[0;33m'; _c_green=$'\033[0;32m'; _c_blue=$'\033[0;34m'; _c_reset=$'\033[0m'

info()    { echo "${_c_blue}==>${_c_reset} $*"; }
success() { echo "${_c_green}==>${_c_reset} $*"; }
warn()    { echo "${_c_yellow}==> warning:${_c_reset} $*" >&2; }
err()     { echo "${_c_red}==> error:${_c_reset} $*" >&2; }
die()     { err "$*"; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found on PATH."
}

# Prompt for exact-text confirmation before a destructive action.
# Usage: confirm_destructive "teardown" "This will delete the cluster's config."
confirm_destructive() {
  local word="$1" msg="${2:-}"
  [[ -n "$msg" ]] && warn "$msg"
  read -r -p "Type '${word}' to confirm: " reply
  [[ "$reply" == "$word" ]] || die "Confirmation text didn't match — aborting."
}

# Repo root = two directories up from this file (bootstrap/lib/common.sh -> repo root)
repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

# Auto-detect the git remote URL and current branch, so nothing has to be
# hardcoded into a script that different people will run from different
# clones/forks. Both are overridable via flags in the calling script.
detect_git_remote_url() {
  git -C "$(repo_root)" remote get-url origin 2>/dev/null || echo ""
}

detect_git_branch() {
  # symbolic-ref (not rev-parse) so this also works on a brand new repo
  # before the first commit exists — same fix as .githooks/pre-commit.
  git -C "$(repo_root)" symbolic-ref --short HEAD 2>/dev/null || echo "main"
}
