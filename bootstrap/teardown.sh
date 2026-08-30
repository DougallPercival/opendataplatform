#!/usr/bin/env bash
# The mirror of install.sh (ARCHITECTURE.md §3, "Tearing it all down"):
# uninstalls every module, removes core, removes Argo CD, then runs k3s's
# own uninstaller. The machine ends up where it was before install.sh ran —
# whether that's a spare box at home or a cloud VM you're about to reclaim.
#
# What this deliberately does NOT do: terminate a cloud instance. That's one
# layer above this script — pair cloud test clusters with Terraform (or
# equivalent) and run `terraform destroy` after this, so nothing lingers.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

YES=false
for arg in "$@"; do
  [[ "$arg" == "--yes" ]] && YES=true
done

if [[ "$YES" != true ]]; then
  confirm_destructive "teardown" \
    "This removes every Argo CD-managed module and core service, then uninstalls k3s itself. There's no undo."
fi

if kubectl get namespace argocd >/dev/null 2>&1; then
  info "Deleting modules-enabled Applications (if any)..."
  kubectl delete applications -n argocd -l platform.io/tier=module --ignore-not-found

  info "Deleting core Applications..."
  kubectl delete applications -n argocd -l platform.io/tier=core --ignore-not-found
  kubectl delete application root -n argocd --ignore-not-found

  info "Waiting for Argo CD to finish pruning (best-effort, 60s)..."
  sleep 60 || true

  info "Removing Argo CD itself..."
  kubectl delete namespace argocd --ignore-not-found --timeout=120s || warn "argocd namespace didn't fully clean up in time — continuing."
else
  info "No argocd namespace found — nothing to prune, moving straight to k3s removal."
fi

if command -v k3s-uninstall.sh >/dev/null 2>&1; then
  info "Running k3s's own uninstaller..."
  k3s-uninstall.sh
  success "k3s removed. This machine is back to bare metal."
elif command -v k3s-agent-uninstall.sh >/dev/null 2>&1; then
  info "Running k3s agent's own uninstaller..."
  k3s-agent-uninstall.sh
  success "k3s agent removed."
else
  warn "No k3s-uninstall.sh found — either k3s isn't installed here, or --skip-k3s was used at install time (in which case this script never touched the underlying cluster, on purpose)."
fi
