#!/usr/bin/env bash
# One-time: creates a Keycloak service-account client ("platform-cli") in the
# "platform" realm, so platform-cli's `workspace invite` command can call the
# Admin REST API to add a user to a workspace's owner/editor/viewer group —
# without ever handling the master-realm bootstrap admin credentials itself.
#
# Why this is a script you run by hand once, not something GitOps applies:
# src/core/auth/realm-platform.yaml's KeycloakRealmImport only CREATES a
# realm — per that file's own header comment, it never updates one that's
# already been imported (yours has, since Phase 0). Adding a `clients:`
# block there and re-syncing would do nothing. This talks to the live Admin
# REST API directly instead — the same thing Keycloak's own kcadm.sh does,
# just via curl+jq so it needs nothing beyond what a Rocky/RHEL/Ubuntu box
# already has or can `dnf`/`apt install jq` trivially, and so this script
# doesn't have to guess at kcadm.sh's TLS-verification flags (not clearly
# documented — checked before writing this, didn't want to hand you an
# unverified flag for a step that actually mutates your Keycloak instance).
#
# Safe to re-run: checks for an existing "platform-cli" client before
# creating one, and reuses it rather than erroring or duplicating.
#
# Requirements (checked at startup via require_cmd — dies immediately with a
# clear message if any is missing, doesn't fail partway through):
#   - kubectl reaching your cluster (uses sudo, same as every other script
#     here — see lib/common.sh's PATH comment for why /usr/local/bin/kubectl
#     is spelled out explicitly rather than relying on PATH through sudo)
#   - curl, base64 — present on essentially every distro by default.
#   - jq — NOT always present by default. Rocky/RHEL/Alma:
#     `sudo dnf install jq`. Ubuntu/Debian: `sudo apt install jq`. macOS:
#     `brew install jq`. Same "verify before you hand someone a script that
#     dies on step one" reasoning as documenting the Python 3.12+ requirement
#     elsewhere in this repo (see catalog-service/platform-sdk/platform-cli's
#     READMEs and docs/known-issues.md).
#
# What it needs beyond tools, and where each comes from:
#   - `platform-initial-admin` (namespace keycloak) — the Keycloak Operator's
#     own auto-generated bootstrap-admin Secret (kubernetes.io/basic-auth:
#     username/password keys). Nothing in this repo creates this; the
#     operator does, the first time the Keycloak CR comes up.
#   - `platform-ca-secret` (namespace cert-manager) — the CA
#     manifests/cluster-issuer.yaml issues keycloak.platform.local's cert
#     from (kubernetes.io/tls: ca.crt among its keys). Used with curl's
#     --cacert so this properly trusts the real issuing CA rather than
#     disabling TLS verification outright.
#
# Networking: keycloak.platform.local isn't resolvable by DNS (no local DNS
# server — see src/core/auth/realm-platform.yaml's comments) and Keycloak's
# hostname provider strictly enforces that hostname for everything past the
# first request (see docs/known-issues.md's "connected fine... broke
# immediately after" entry — a real incident, not a hypothetical). Rather
# than asking you to add an /etc/hosts entry, this script port-forwards to
# 127.0.0.1 itself and uses curl's --resolve to send real requests to
# 127.0.0.1 while both the TLS SNI and the Host header still say
# keycloak.platform.local — satisfies the hostname provider without editing
# any system files.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

KUBECTL="sudo /usr/local/bin/kubectl"
KEYCLOAK_HOST="keycloak.platform.local"
KEYCLOAK_PORT="8443"
REALM="platform"
CLIENT_ID="platform-cli"
PORT_FORWARD_LOCAL_PORT="18443"   # unlikely to collide with anything else already running

require_cmd curl
require_cmd jq
require_cmd base64

work_dir="$(mktemp -d)"
pf_pid=""
cleanup() {
  if [[ -n "$pf_pid" ]]; then
    kill "$pf_pid" >/dev/null 2>&1 || true
  fi
  rm -rf "$work_dir"
}
trap cleanup EXIT

info "Extracting platform-ca cert from cert-manager..."
$KUBECTL get secret platform-ca-secret -n cert-manager -o jsonpath='{.data.ca\.crt}' \
  | base64 -d > "${work_dir}/platform-ca.crt"
[[ -s "${work_dir}/platform-ca.crt" ]] || die \
  "platform-ca.crt came back empty — check platform-ca-secret has a ca.crt key: \
${KUBECTL} get secret platform-ca-secret -n cert-manager -o yaml"

info "Reading the Keycloak Operator's bootstrap admin credentials..."
ADMIN_USER="$($KUBECTL get secret platform-initial-admin -n keycloak -o jsonpath='{.data.username}' | base64 -d)"
ADMIN_PASS="$($KUBECTL get secret platform-initial-admin -n keycloak -o jsonpath='{.data.password}' | base64 -d)"
[[ -n "$ADMIN_USER" && -n "$ADMIN_PASS" ]] || die \
  "Couldn't read platform-initial-admin's username/password — check it exists: \
${KUBECTL} get secret platform-initial-admin -n keycloak"

info "Port-forwarding to Keycloak (127.0.0.1:${PORT_FORWARD_LOCAL_PORT} -> platform-service:${KEYCLOAK_PORT})..."
$KUBECTL port-forward -n keycloak svc/platform-service \
  "${PORT_FORWARD_LOCAL_PORT}:${KEYCLOAK_PORT}" >"${work_dir}/port-forward.log" 2>&1 &
pf_pid=$!

# Poll rather than a fixed sleep — port-forward is usually ready in well
# under a second, but a fixed sleep either wastes time or isn't enough on a
# loaded box; this waits for exactly as long as it actually takes, up to 10s.
ready=false
for _ in $(seq 1 20); do
  if curl -sS -o /dev/null --connect-timeout 1 "https://127.0.0.1:${PORT_FORWARD_LOCAL_PORT}/" -k; then
    ready=true
    break
  fi
  sleep 0.5
done
[[ "$ready" == "true" ]] || die \
  "Port-forward never came up — check ${work_dir}/port-forward.log (this dir is deleted on exit, \
so rerun with 'set -x' or comment out the trap if you need to inspect it)."

# --resolve sends the request to 127.0.0.1 while TLS SNI and the Host header
# both say keycloak.platform.local — this is what satisfies Keycloak's
# hostname provider (see this script's header comment) without touching
# /etc/hosts. --cacert is the real trust fix, not --insecure/-k (used only
# above, for the readiness probe, where we don't care about the cert yet).
CURL=(curl -sS --fail-with-body
  --resolve "${KEYCLOAK_HOST}:${PORT_FORWARD_LOCAL_PORT}:127.0.0.1"
  --cacert "${work_dir}/platform-ca.crt")
BASE_URL="https://${KEYCLOAK_HOST}:${PORT_FORWARD_LOCAL_PORT}"

info "Getting a master-realm admin token..."
TOKEN_RESPONSE="$("${CURL[@]}" -X POST "${BASE_URL}/realms/master/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=admin-cli \
  -d "username=${ADMIN_USER}" -d "password=${ADMIN_PASS}")" || die \
  "Admin token request failed — output above (if any) is Keycloak's own error body."
TOKEN="$(jq -r '.access_token // empty' <<<"$TOKEN_RESPONSE")"
[[ -n "$TOKEN" ]] || die "No access_token in the response: ${TOKEN_RESPONSE}"

AUTH=(-H "Authorization: Bearer ${TOKEN}")

info "Checking whether '${CLIENT_ID}' already exists in realm '${REALM}'..."
CLIENT_UUID="$("${CURL[@]}" "${AUTH[@]}" "${BASE_URL}/admin/realms/${REALM}/clients?clientId=${CLIENT_ID}" \
  | jq -r '.[0].id // empty')"

if [[ -n "$CLIENT_UUID" ]]; then
  success "Client '${CLIENT_ID}' already exists (id=${CLIENT_UUID}) — reusing it."
else
  info "Creating client '${CLIENT_ID}' (confidential, service-account only — no login flow)..."
  "${CURL[@]}" "${AUTH[@]}" -X POST "${BASE_URL}/admin/realms/${REALM}/clients" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg id "$CLIENT_ID" '{
      clientId: $id,
      protocol: "openid-connect",
      publicClient: false,
      serviceAccountsEnabled: true,
      standardFlowEnabled: false,
      directAccessGrantsEnabled: false
    }')"
  CLIENT_UUID="$("${CURL[@]}" "${AUTH[@]}" "${BASE_URL}/admin/realms/${REALM}/clients?clientId=${CLIENT_ID}" \
    | jq -r '.[0].id')"
  [[ -n "$CLIENT_UUID" && "$CLIENT_UUID" != "null" ]] || die "Client creation looked like it succeeded but a follow-up lookup found nothing."
  success "Created client '${CLIENT_ID}' (id=${CLIENT_UUID})."
fi

info "Finding the client's service-account user..."
SA_USER_ID="$("${CURL[@]}" "${AUTH[@]}" "${BASE_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}/service-account-user" \
  | jq -r .id)"

info "Finding realm-management's 'manage-users' client role..."
# manage-users, not a narrower fine-grained-admin-permissions grant scoped to
# just the three workspace groups — the fine-grained route exists in
# Keycloak and would be tighter, but it's real added setup complexity for a
# personal/solo deployment; manage-users is the standard, well-documented
# role for "this client can manage user accounts and group membership,"
# which is exactly what `workspace invite` needs. Revisit if this ever runs
# somewhere the blast radius of that role actually matters.
REALM_MGMT_ID="$("${CURL[@]}" "${AUTH[@]}" "${BASE_URL}/admin/realms/${REALM}/clients?clientId=realm-management" \
  | jq -r '.[0].id')"
MANAGE_USERS_ROLE="$("${CURL[@]}" "${AUTH[@]}" "${BASE_URL}/admin/realms/${REALM}/clients/${REALM_MGMT_ID}/roles/manage-users")"

info "Granting 'manage-users' to the service account..."
"${CURL[@]}" "${AUTH[@]}" -X POST "${BASE_URL}/admin/realms/${REALM}/users/${SA_USER_ID}/role-mappings/clients/${REALM_MGMT_ID}" \
  -H "Content-Type: application/json" -d "[${MANAGE_USERS_ROLE}]" \
  || warn "Role assignment request failed — if this is a rerun and the role's already assigned, that's likely a harmless duplicate-assignment error; check with the verification step below either way."

info "Fetching the client secret..."
CLIENT_SECRET="$("${CURL[@]}" "${AUTH[@]}" "${BASE_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}/client-secret" \
  | jq -r .value)"
[[ -n "$CLIENT_SECRET" && "$CLIENT_SECRET" != "null" ]] || die "Client secret came back empty."

info "Storing as Secret 'platform-keycloak-cli-credentials' (namespace keycloak)..."
$KUBECTL create secret generic platform-keycloak-cli-credentials -n keycloak \
  --from-literal=client_id="${CLIENT_ID}" \
  --from-literal=client_secret="${CLIENT_SECRET}" \
  --dry-run=client -o yaml | $KUBECTL apply -f -

success "Done."
cat <<EOF

To use platform-cli's 'workspace invite' from this machine:
  export PLATFORM_KEYCLOAK_CLIENT_SECRET="${CLIENT_SECRET}"

To read it back later instead of rerunning this script:
  ${KUBECTL} get secret platform-keycloak-cli-credentials -n keycloak -o jsonpath='{.data.client_secret}' | base64 -d
EOF
