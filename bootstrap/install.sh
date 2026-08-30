#!/usr/bin/env bash
# The one unavoidable script (ARCHITECTURE.md §3). Brings a bare machine up
# to "Argo CD is reconciling this repo's core/" — everything after that is
# a choice made in the shell/CLI/git, not another script to run.
#
# What it does:
#   1. Installs k3s if one isn't already running (control-node role,
#      traefik + servicelb disabled — we bring our own ingress + MetalLB).
#   2. Installs Argo CD (official stable manifest).
#   3. Applies ONE Application (the "app of apps") pointing Argo CD at
#      src/core/argocd/apps in THIS repo, on the branch you're currently on
#      (or --revision, if you want it tracking something else).
#
# From here on, adding/removing anything is a git operation, not a script —
# see the Add-ons page / platform-cli / modules-enabled/ in ARCHITECTURE.md §3.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

ROLE="control"
REPO_URL=""
REVISION=""
ARGOCD_VERSION="stable"
SKIP_K3S=false

usage() {
  cat <<EOF
Usage: bootstrap/install.sh [options]

  --repo-url <url>     Git remote for Argo CD to track (default: this clone's 'origin')
  --revision <branch>  Branch/tag for Argo CD to track (default: current branch)
  --role <role>        Node role for this machine: control|storage|compute (default: control)
  --skip-k3s           Don't touch k3s — use it against an existing cluster (any distro)
  -h, --help            Show this help

Phase 0 gets you: k3s, Argo CD, and the app-of-apps handoff. MetalLB's IP pool and the ingress/
cert-manager issuer still need values filled in for YOUR network — see the TODOs Argo CD will
surface as it syncs (src/core/argocd/manifests/).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --revision) REVISION="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --skip-k3s) SKIP_K3S=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

REPO_ROOT="$(repo_root)"
[[ -z "$REPO_URL" ]] && REPO_URL="$(detect_git_remote_url)"
[[ -z "$REVISION" ]] && REVISION="$(detect_git_branch)"
[[ -z "$REPO_URL" ]] && die "Couldn't detect a git remote URL — pass --repo-url explicitly."

info "Repo:     $REPO_URL"
info "Revision: $REVISION"
info "Role:     $ROLE"

# ---- 1. k3s -----------------------------------------------------------
if [[ "$SKIP_K3S" == true ]]; then
  info "Skipping k3s install (--skip-k3s) — using the cluster kubectl is already pointed at."
  require_cmd kubectl
elif command -v k3s >/dev/null 2>&1; then
  info "k3s already installed — leaving it as-is."
else
  require_cmd curl
  info "Installing k3s (role: ${ROLE})..."
  curl -sfL https://get.k3s.io | \
    INSTALL_K3S_EXEC="--disable traefik --disable servicelb --node-label platform.io/role=${ROLE}" \
    sh -
  # k3s ships kubectl at /usr/local/bin/k3s; symlink kept by the installer as
  # /usr/local/bin/kubectl normally. Point KUBECONFIG at k3s's own file for
  # the rest of this script either way.
  export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
  info "Waiting for the node to report Ready..."
  until kubectl get nodes 2>/dev/null | grep -q " Ready"; do sleep 2; done
  success "k3s is up."
fi

# ---- 2. Argo CD ---------------------------------------------------------
require_cmd kubectl
if kubectl get namespace argocd >/dev/null 2>&1; then
  info "Argo CD namespace already exists — leaving the existing install in place."
else
  info "Installing Argo CD (${ARGOCD_VERSION})..."
  kubectl create namespace argocd
  kubectl apply -n argocd -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
  info "Waiting for the Argo CD API server to be ready (this can take a couple of minutes)..."
  kubectl -n argocd wait --for=condition=available --timeout=300s deployment/argocd-server
  success "Argo CD is up."
fi

# ---- 3. Hand Argo CD the app-of-apps ------------------------------------
info "Applying the root Application (src/core/argocd/apps)..."
sed \
  -e "s|__REPO_URL__|${REPO_URL}|g" \
  -e "s|__REVISION__|${REVISION}|g" \
  "${REPO_ROOT}/src/core/argocd/root-app.yaml" | kubectl apply -f -

success "Done. Argo CD is reconciling src/core/argocd/apps from ${REPO_URL}@${REVISION}."
echo ""
echo "  Watch it sync:   kubectl get applications -n argocd -w"
echo "  Argo CD UI:       kubectl -n argocd port-forward svc/argocd-server 8080:443"
echo "                     (initial admin password: kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
echo ""
echo "  Still TODO before core is fully healthy — see src/core/argocd/manifests/:"
echo "   - MetalLB's IP address pool (your actual LAN/cloud subnet)"
echo "   - cert-manager's ClusterIssuer (ships self-signed by default)"
echo "   - Keycloak's database connection"
