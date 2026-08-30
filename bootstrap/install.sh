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
# (common.sh above also fixes up PATH for /usr/local/bin/kubectl — see its comment)

ROLE="control"
REPO_URL=""
REVISION=""
ARGOCD_VERSION="stable"
SKIP_K3S=false
REPO_SSH_KEY=""

usage() {
  cat <<EOF
Usage: bootstrap/install.sh [options]

  --repo-url <url>     Git remote for Argo CD to track (default: this clone's 'origin')
  --revision <branch>  Branch/tag for Argo CD to track (default: current branch)
  --role <role>        Node role for this machine: control|storage|compute (default: control)
  --skip-k3s           Don't touch k3s — use it against an existing cluster (any distro)
  --repo-ssh-key <path> Path to an SSH private key (a read-only deploy key is enough) that
                        Argo CD's repo-server should use to clone REPO_URL. Only needed if
                        REPO_URL is private. Registers it as a repository credential Secret
                        in the argocd namespace, so you don't have to do this by hand after
                        every install (it lives in the argocd namespace, so a teardown that
                        deletes that namespace takes it with it — pass this again on re-install
                        rather than repeating docs/known-issues.md's manual steps).
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
    --repo-ssh-key) REPO_SSH_KEY="$2"; shift 2 ;;
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
  require_cmd kubectl
  info "Waiting for the node to report Ready..."
  _waited=0
  until kubectl get nodes 2>/dev/null | grep -q " Ready"; do
    sleep 2
    _waited=$((_waited + 2))
    if [[ $_waited -ge 180 ]]; then
      die "Node still not Ready after 3 minutes. Check directly: kubectl get nodes / sudo journalctl -u k3s -n 100"
    fi
  done
  success "k3s is up."
fi

# ---- 2. Argo CD ---------------------------------------------------------
require_cmd kubectl
if kubectl get namespace argocd >/dev/null 2>&1; then
  info "Argo CD namespace already exists — leaving the existing install in place."
else
  info "Installing Argo CD (${ARGOCD_VERSION})..."
  kubectl create namespace argocd
  # --server-side: Argo CD's CRDs (applicationsets.argoproj.io especially) are
  # big enough that plain client-side `kubectl apply` — which stashes the
  # whole previous config into a last-applied-configuration annotation for
  # diffing — blows past Kubernetes' 256KiB annotation limit. Server-side
  # apply tracks field ownership on the API server instead, sidestepping the
  # annotation entirely. --force-conflicts so a re-run of this script doesn't
  # error out over field ownership from its own previous run.
  kubectl apply --server-side --force-conflicts -n argocd -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
  info "Waiting for the Argo CD API server to be ready (this can take a couple of minutes)..."
  kubectl -n argocd wait --for=condition=available --timeout=300s deployment/argocd-server
  success "Argo CD is up."
fi

# ---- 2b. Repo credentials (only if REPO_URL is private) -----------------
if [[ -n "$REPO_SSH_KEY" ]]; then
  [[ -f "$REPO_SSH_KEY" ]] || die "--repo-ssh-key path not found: $REPO_SSH_KEY"
  info "Registering repo credentials with Argo CD's repo-server..."
  # Argo CD's repo-server has no credentials of its own by default — cloning
  # this repo from the host shell (e.g. the deploy key used for `git clone`
  # here) does NOT give the repo-server pod any access. Without this, a
  # private REPO_URL fails with something like: "error creating SSH agent:
  # SSH agent requested but SSH_AUTH_SOCK not-specified". Apply is idempotent
  # (dry-run|apply) so re-running install.sh with the same key is a no-op.
  kubectl -n argocd create secret generic gitops-repo-credentials \
    --from-literal=type=git \
    --from-literal=url="${REPO_URL}" \
    --from-file=sshPrivateKey="${REPO_SSH_KEY}" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n argocd label secret gitops-repo-credentials argocd.argoproj.io/secret-type=repository --overwrite
  success "Repo credentials registered."
else
  info "No --repo-ssh-key given — skipping repo credential setup (fine if REPO_URL is public; see docs/known-issues.md if it isn't)."
fi

# ---- 2c. Sanity-check MetalLB's committed range against this host's subnet
# metallb-pool.yaml's IP range has to be real, network-specific values (see
# docs/known-issues.md — there's no way to derive "which slice of your subnet
# is safe" automatically, so it's committed as a concrete range, not a
# placeholder like __REPO_URL__). That means it's silently wrong if this repo
# ever runs against different hardware/network than it was set up on — and
# MetalLB's L2Advertisement mode doesn't just risk a conflict in that case, it
# doesn't work AT ALL, since it ARPs on the local subnet. Warn loudly here
# rather than let that surface later as "ingress-nginx's external IP never
# leaves <pending>" with no obvious cause.
if command -v ip >/dev/null 2>&1; then
  POOL_FILE="${REPO_ROOT}/src/core/argocd/manifests/metallb-pool.yaml"
  if [[ -f "$POOL_FILE" ]]; then
    POOL_FIRST_IP="$(grep -m1 -oE '([0-9]{1,3}\.){3}[0-9]{1,3}-' "$POOL_FILE" | head -1 | sed 's/-$//')"
    HOST_CIDR="$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' | head -1)"
    if [[ -n "$POOL_FIRST_IP" && -n "$HOST_CIDR" ]]; then
      POOL_PREFIX="$(cut -d. -f1-3 <<< "$POOL_FIRST_IP")"
      HOST_PREFIX="$(cut -d. -f1-3 <<< "$HOST_CIDR")"
      if [[ "$POOL_PREFIX" != "$HOST_PREFIX" ]]; then
        warn "metallb-pool.yaml's IP range (${POOL_PREFIX}.x) doesn't match this host's detected subnet (${HOST_CIDR}, ${HOST_PREFIX}.x)."
        warn "MetalLB needs its pool on the SAME subnet as this host to actually work — a mismatch here usually means"
        warn "this repo was set up on different hardware/network than it's running on now. Update"
        warn "src/core/argocd/manifests/metallb-pool.yaml before relying on ingress-nginx getting a working external IP."
        warn "Continuing anyway — this is a warning, not a blocker."
      else
        info "metallb-pool.yaml's IP range matches this host's subnet (${HOST_PREFIX}.x)."
      fi
    fi
  fi
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
