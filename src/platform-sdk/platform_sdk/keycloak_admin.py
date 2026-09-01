"""KeycloakAdminClient — talks to Keycloak's own Admin REST API to add an
existing user to a workspace's owner/editor/viewer group
(src/core/auth/realm-platform.yaml's `/workspaces/<name>/<role>` model).
This is `platform workspace invite`'s entire implementation.
catalog-service isn't involved at all — see that service's
app/routers/workspaces.py docstring: membership is Keycloak-group
territory, not catalog data, so there's nothing here for catalog-service to
proxy or store.

Why this manages its own `kubectl port-forward` and CA cert, unlike
PlatformClient (which just expects PLATFORM_CATALOG_URL to already be
reachable, however the caller got it there): Keycloak's hostname provider
strictly enforces `spec.hostname.hostname: keycloak.platform.local` on every
request once it's set — see docs/known-issues.md's 2026-08-31 entry. A plain
port-forward to `localhost`/a raw IP doesn't satisfy it the way it does for
catalog-service (a bare FastAPI/uvicorn app that doesn't care what Host
header it gets); Keycloak's TLS SNI and Host header both have to say
`keycloak.platform.local`. Fixing that by asking every caller to hand-edit
/etc/hosts would work but is exactly what
bootstrap/keycloak-bootstrap-cli-client.sh's own header comment already
decided against for a ONE-TIME script — doubly true for something meant to
run repeatedly as part of normal `platform` usage. So this class reproduces
curl's `--resolve HOST:PORT:IP` trick in Python (temporarily patching
`socket.getaddrinfo` for just `keycloak.platform.local`, scoped to this
client's own lifetime and undone on close — see `_ResolvePatch` below) and
manages its own short-lived port-forward + extracted CA cert the same way
the bootstrap script does. Net effect: `platform workspace invite alice
--role editor` stays one command, not "first go start a port-forward
yourself, then remember the right curl flags."

Authenticates as the `platform-cli` Keycloak client itself (client_credentials
grant, PLATFORM_KEYCLOAK_CLIENT_SECRET from config.py) — never the master-realm
bootstrap admin. The bootstrap script used the master admin once, to create
this narrower client in the first place; everything from here on uses that
narrower client, scoped to exactly `manage-users` in the `platform` realm.

Scope boundary worth restating here, not just in realm-platform.yaml: this
does NOT create Keycloak users. `registrationAllowed: false` and no seeded
users are deliberate (see that file's header comment) — `invite()` raises a
clear KeycloakAdminError naming that if the username doesn't already exist,
rather than a bare 404.
"""
from __future__ import annotations

import base64
import contextlib
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from platform_sdk.config import SDKSettings
from platform_sdk.exceptions import KeycloakAdminError
from platform_sdk.models import InviteResult, Role


class _ResolvePatch:
    """Reproduces curl's `--resolve HOST:PORT:IP` for Python's stdlib socket
    resolution. httpx's sync transport (via httpcore) resolves hosts through
    `socket.getaddrinfo` like everything else in the stdlib, so patching
    that one function has the same effect curl's --resolve flag has — TLS
    SNI and the Host header still say `keycloak.platform.local` (that's
    still the URL host), only the actual TCP connection goes to `target_ip`
    — without editing /etc/hosts. Scoped to one hostname and undone via
    `undo()`, not left patched process-wide forever.
    """

    def __init__(self, hostname: str, target_ip: str) -> None:
        self._hostname = hostname
        self._target_ip = target_ip
        self._original = socket.getaddrinfo

    def apply(self) -> None:
        original = self._original
        hostname = self._hostname
        target_ip = self._target_ip

        def patched(host, *args, **kwargs):
            if host == hostname:
                host = target_ip
            return original(host, *args, **kwargs)

        socket.getaddrinfo = patched

    def undo(self) -> None:
        socket.getaddrinfo = self._original


class _PortForward:
    """Manages a background `kubectl port-forward` to Keycloak's Service
    plus the CA cert `--cacert` needs, the same way
    bootstrap/keycloak-bootstrap-cli-client.sh does it — see that script's
    header comment for the original design this mirrors, and this module's
    own docstring for why KeycloakAdminClient needs one of its own at all.
    """

    def __init__(
        self, *, kubectl_cmd: str, namespace: str, service_name: str, service_port: int, local_port: int
    ) -> None:
        self._kubectl_cmd = kubectl_cmd.split()
        self._namespace = namespace
        self._service_name = service_name
        self._service_port = service_port
        self._local_port = local_port
        self._process: subprocess.Popen | None = None
        self._ca_cert_path: Path | None = None

    def start(self) -> str:
        ca_cert_path = self._extract_ca_cert()
        self._process = subprocess.Popen(
            [
                *self._kubectl_cmd,
                "port-forward",
                "-n",
                self._namespace,
                f"svc/{self._service_name}",
                f"{self._local_port}:{self._service_port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready()
        return ca_cert_path

    def _extract_ca_cert(self) -> str:
        # Same Secret bootstrap/keycloak-bootstrap-cli-client.sh reads —
        # see that script's header comment for where it comes from
        # (cert-manager's platform-ca ClusterIssuer).
        try:
            result = subprocess.run(
                [
                    *self._kubectl_cmd,
                    "get",
                    "secret",
                    "platform-ca-secret",
                    "-n",
                    "cert-manager",
                    "-o",
                    r"jsonpath={.data.ca\.crt}",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise KeycloakAdminError(
                f"Couldn't read platform-ca-secret's ca.crt via kubectl: {exc.stderr.strip()}"
            ) from exc
        ca_bytes = base64.b64decode(result.stdout)
        if not ca_bytes:
            raise KeycloakAdminError(
                "platform-ca-secret's ca.crt came back empty — check it exists: "
                "kubectl get secret platform-ca-secret -n cert-manager -o yaml"
            )
        fd = tempfile.NamedTemporaryFile(prefix="platform-ca-", suffix=".crt", delete=False)
        fd.write(ca_bytes)
        fd.close()
        self._ca_cert_path = Path(fd.name)
        return fd.name

    def _wait_ready(self) -> None:
        # Poll rather than a fixed sleep — same reasoning as the bootstrap
        # script's own readiness loop.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError):
                with socket.create_connection(("127.0.0.1", self._local_port), timeout=0.5):
                    return
            time.sleep(0.3)
        raise KeycloakAdminError(
            f"kubectl port-forward to {self._service_name}:{self._service_port} never came up on "
            f"127.0.0.1:{self._local_port} within 10s. Is the 'keycloak' namespace's platform-service "
            "Service up? (sudo kubectl get svc -n keycloak)"
        )

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            with contextlib.suppress(Exception):
                self._process.wait(timeout=5)
            self._process = None
        if self._ca_cert_path is not None:
            with contextlib.suppress(OSError):
                self._ca_cert_path.unlink()
            self._ca_cert_path = None


class KeycloakAdminClient:
    def __init__(
        self,
        *,
        host: str | None = None,
        realm: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        namespace: str | None = None,
        service_name: str | None = None,
        service_port: int | None = None,
        local_port: int | None = None,
        kubectl_cmd: str | None = None,
        settings: SDKSettings | None = None,
        timeout: float = 10.0,
        _client: httpx.Client | None = None,
    ) -> None:
        # Same explicit-arg > settings precedence as PlatformClient — see
        # that class's constructor comment.
        settings = settings or SDKSettings()
        self._host = host or settings.keycloak_host
        self._realm = realm or settings.keycloak_realm
        self._client_id = client_id or settings.keycloak_client_id
        self._client_secret = client_secret or settings.keycloak_client_secret
        if not self._client_secret:
            raise KeycloakAdminError(
                "No Keycloak client secret configured. Set PLATFORM_KEYCLOAK_CLIENT_SECRET — see "
                "bootstrap/keycloak-bootstrap-cli-client.sh's printed 'export' line, or read it back "
                "with the command that script also prints if you've already run it."
            )
        self._namespace = namespace or settings.keycloak_namespace
        self._service_name = service_name or settings.keycloak_service_name
        self._service_port = service_port or settings.keycloak_service_port
        self._local_port = local_port or settings.keycloak_local_port
        self._kubectl_cmd = kubectl_cmd or settings.keycloak_kubectl_cmd
        self._timeout = timeout
        # Tests construct this with `_client` already set to a mocked
        # httpx.Client (respx) — skips the real port-forward/getaddrinfo
        # plumbing entirely, same "inject a transport for tests" shape
        # PlatformClient's own tests use, just via a private constructor arg
        # instead since PlatformClient never needed one. __exit__ only tears
        # down what __enter__ actually built.
        self._external_client = _client is not None
        self._client = _client
        self._token: str | None = None
        self._port_forward: _PortForward | None = None
        self._resolve_patch: _ResolvePatch | None = None

    def __enter__(self) -> KeycloakAdminClient:
        if self._client is None:
            self._port_forward = _PortForward(
                kubectl_cmd=self._kubectl_cmd,
                namespace=self._namespace,
                service_name=self._service_name,
                service_port=self._service_port,
                local_port=self._local_port,
            )
            ca_cert_path = self._port_forward.start()
            self._resolve_patch = _ResolvePatch(self._host, "127.0.0.1")
            self._resolve_patch.apply()
            self._client = httpx.Client(
                base_url=f"https://{self._host}:{self._local_port}",
                verify=ca_cert_path,
                timeout=self._timeout,
            )
        self._token = self._fetch_token()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if not self._external_client and self._client is not None:
            self._client.close()
            self._client = None
        if self._resolve_patch is not None:
            self._resolve_patch.undo()
            self._resolve_patch = None
        if self._port_forward is not None:
            self._port_forward.stop()
            self._port_forward = None

    # ---- token + low-level request plumbing ---------------------------
    def _fetch_token(self) -> str:
        # client_credentials against the *platform* realm's own token
        # endpoint — this authenticates AS the platform-cli client (its own
        # secret), not as the master-realm bootstrap admin the bootstrap
        # script used once to create that client in the first place.
        response = self._client.post(
            f"/realms/{self._realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if response.status_code >= 400:
            raise KeycloakAdminError(
                f"Failed to get a Keycloak admin token for client {self._client_id!r}: "
                f"{response.status_code} {response.text}"
            )
        token = response.json().get("access_token")
        if not token:
            raise KeycloakAdminError(f"Token response had no access_token: {response.text}")
        return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self._client.request(method, path, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            raise KeycloakAdminError(f"{method} {path} -> {response.status_code}: {response.text}")
        return response

    # ---- groups ---------------------------------------------------------
    def _group_by_path(self, path: str) -> str | None:
        # GET /admin/realms/{realm}/group-by-path/{path} — {path} takes the
        # segments literally (no leading slash, no URL-encoding of the
        # internal slashes): confirmed against Keycloak's own Admin REST API
        # docs rather than assumed, same "verify before a live-mutating
        # script depends on it" discipline as the bootstrap script's header
        # comment describes for kcadm.sh.
        response = self._client.get(
            f"/admin/realms/{self._realm}/group-by-path/{path}", headers=self._headers()
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise KeycloakAdminError(f"GET group-by-path/{path} -> {response.status_code}: {response.text}")
        return response.json()["id"]

    def _get_or_create_group(self, path: str, parent_id: str | None, name: str) -> str:
        group_id = self._group_by_path(path)
        if group_id is not None:
            return group_id
        create_path = (
            f"/admin/realms/{self._realm}/groups"
            if parent_id is None
            else f"/admin/realms/{self._realm}/groups/{parent_id}/children"
        )
        response = self._client.post(create_path, headers=self._headers(), json={"name": name})
        # 409 = another invite (or this same call, retried) created it
        # between the lookup above and this POST — not fatal, just look it
        # up again rather than treating a race as an error.
        if response.status_code not in (201, 409):
            raise KeycloakAdminError(f"Creating group {path!r} -> {response.status_code}: {response.text}")
        group_id = self._group_by_path(path)
        if group_id is None:
            raise KeycloakAdminError(
                f"Created (or hit a conflict creating) group {path!r} but a follow-up lookup found nothing."
            )
        return group_id

    def _map_realm_role(self, group_id: str, role_name: str) -> None:
        role_repr = self._request("GET", f"/admin/realms/{self._realm}/roles/{role_name}").json()
        self._request(
            "POST", f"/admin/realms/{self._realm}/groups/{group_id}/role-mappings/realm", json=[role_repr]
        )

    def _ensure_role_group(self, workspace: str, role: Role) -> tuple[str, bool]:
        # The common case: `platform workspace invite` against "personal"
        # (or any workspace someone already invited into before) — the
        # group already exists, one lookup, done.
        role_path = f"workspaces/{workspace}/{role.value}"
        existing = self._group_by_path(role_path)
        if existing is not None:
            return existing, False

        # Self-healing path: `platform workspace create` (catalog-service's
        # own endpoint) never touches Keycloak — see that service's
        # app/routers/workspaces.py docstring — so a workspace created that
        # way has no matching group yet. Rather than fail with "no such
        # group" and leave a manual Keycloak admin-console step as the only
        # fix, build the missing part of the path here. "workspaces" itself
        # is always present (seeded by realm-platform.yaml at import time),
        # so only the workspace subgroup and/or the role subgroup can
        # actually be missing.
        workspaces_id = self._group_by_path("workspaces")
        if workspaces_id is None:
            raise KeycloakAdminError(
                "No top-level 'workspaces' group in the Keycloak realm — realm-platform.yaml should "
                "have seeded this when the realm was first imported. This isn't just a missing "
                "workspace; check the realm itself in Keycloak's admin console before retrying."
            )
        workspace_id = self._get_or_create_group(f"workspaces/{workspace}", workspaces_id, workspace)
        role_id = self._get_or_create_group(role_path, workspace_id, role.value)
        self._map_realm_role(role_id, role.value)
        return role_id, True

    # ---- users ------------------------------------------------------------
    def _find_user_id(self, username: str) -> str:
        response = self._request(
            "GET", f"/admin/realms/{self._realm}/users", params={"username": username, "exact": "true"}
        )
        users = response.json()
        if not users:
            raise KeycloakAdminError(
                f"No Keycloak user named {username!r} in realm {self._realm!r}. `platform workspace "
                "invite` doesn't create users — src/core/auth/realm-platform.yaml sets "
                "registrationAllowed: false and seeds no users on purpose (see that file's header "
                "comment). The user has to already exist in Keycloak some other way first."
            )
        return users[0]["id"]

    # ---- the actual command -------------------------------------------
    def invite(self, username: str, *, workspace: str = "personal", role: Role = Role.VIEWER) -> InviteResult:
        user_id = self._find_user_id(username)
        group_id, created = self._ensure_role_group(workspace, role)
        # PUT is idempotent here — re-inviting someone who's already a
        # member just succeeds again (204), so `invite()` doesn't need its
        # own "already a member" check first.
        self._request("PUT", f"/admin/realms/{self._realm}/users/{user_id}/groups/{group_id}")
        return InviteResult(
            username=username,
            workspace=workspace,
            role=role,
            group_path=f"/workspaces/{workspace}/{role.value}",
            group_created=created,
        )
