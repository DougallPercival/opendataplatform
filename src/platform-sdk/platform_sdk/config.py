"""Where PlatformClient's defaults come from when a caller doesn't pass them
explicitly — env vars (optionally via a `.env` file), same
pydantic-settings pattern catalog-service/app/config.py already uses, for
the same reason: one typed, validated place instead of scattered
`os.environ.get(...)` calls.

Deliberately NOT a config *file* (no `~/.platform/config.toml`) yet — env
vars are enough for "one person, one workspace, running this from a shell
or a CI job," which is everything this SDK/CLI needs to support today. A
config file becomes worth it once there's something that actually needs
persisted-across-sessions state a `PLATFORM_*` env var can't reasonably
hold — that line already got crossed once (credentials.py's
~/.config/platform/credentials.json, for `platform login`'s tokens), but
that's deliberately its own dedicated file, not folded into this settings
object: settings here are re-resolved fresh from the environment on every
`SDKSettings()` construction, while credentials are read-write state a
login command creates and a completely separate later process reads back.
Mixing the two would make this class's "just env vars, resolved fresh"
contract a lie.

`user`/`role` fields REMOVED (2026-09-02, platform-gateway-auth branch):
identity and role are no longer anything PlatformClient declares — they're
derived server-side, by gateway, from the caller's verified Keycloak token
(see client.py's module docstring, and src/core/gateway/app/auth.py). A
`PLATFORM_USER`/`PLATFORM_ROLE` env var that no longer did anything would be
actively misleading, not harmlessly unused — better to remove them and let
`platform_cli`'s `--user`/`--role` flags fail with "no such option" than
leave either silently ignored.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class SDKSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLATFORM_", env_file=".env", env_file_encoding="utf-8")

    # RENAMED from catalog_url/PLATFORM_CATALOG_URL (2026-09-02,
    # platform-gateway-auth branch): the old name became actively
    # misleading once PlatformClient stopped talking to catalog-service
    # directly — every request now goes through platform-gateway, which
    # verifies the caller's token and forwards a derived, trustworthy
    # request to catalog-service on the client's behalf (see client.py's
    # module docstring). Default is gateway's own port-forward address
    # (`kubectl port-forward -n gateway svc/gateway 8080:8080`), the same
    # "match the service's own README without either side hardcoding
    # knowledge of the other" reasoning catalog_url's old default used.
    gateway_url: str = "http://localhost:8080"
    workspace: str = "personal"

    # --- Keycloak Admin API (platform_sdk.keycloak_admin, `platform
    # workspace invite`) --- Separate from gateway_url/workspace above: this
    # talks to Keycloak directly, not through catalog-service or gateway
    # (see catalog-service/app/routers/workspaces.py's own docstring on why
    # membership is Keycloak-group territory, not catalog data). See
    # keycloak_admin.py's module docstring for the full "why a port-forward
    # plus a hostname-resolution patch, and not just a URL" design.
    keycloak_host: str = "keycloak.platform.local"
    keycloak_realm: str = "platform"
    keycloak_client_id: str = "platform-cli"
    # No default, on purpose: KeycloakAdminClient refuses to start rather
    # than silently doing nothing, with an error naming exactly where to get
    # this (bootstrap/keycloak-bootstrap-cli-client.sh's printed `export`
    # line, or its read-back command for a rerun).
    keycloak_client_secret: str | None = None
    keycloak_namespace: str = "keycloak"
    keycloak_service_name: str = "platform-service"
    keycloak_service_port: int = 8443
    # Deliberately the same local port the bootstrap script uses — one
    # number to remember, and the two never run at once in practice (the
    # bootstrap script is a one-time setup step, this is the ongoing path).
    keycloak_local_port: int = 18443
    # Same PATH-under-sudo reasoning as bootstrap/lib/common.sh's own
    # require_cmd comments — spelled out explicitly rather than trusting
    # sudo's secure_path to include /usr/local/bin.
    keycloak_kubectl_cmd: str = "sudo /usr/local/bin/kubectl"

    # --- Keycloak device-flow login (platform_sdk.keycloak_login,
    # `platform login`) --- A separate client from keycloak_client_id above
    # on purpose (see bootstrap/keycloak-bootstrap-login-client.sh's header):
    # that one is confidential/service-account-only and authenticates AS
    # platform-cli itself; this one is public and authenticates AS a real
    # human via the device grant, and must never carry a secret a leaked CLI
    # binary could expose.
    keycloak_login_client_id: str = "platform-cli-login"
    # A different local port from keycloak_local_port's 18443, not the same
    # one reused — `platform login` and a command that also needs
    # KeycloakAdminClient's self-heal path (e.g. `workspace invite` into a
    # brand new workspace) can plausibly run back-to-back, and a
    # still-tearing-down previous port-forward on a shared local port is a
    # real, seen-elsewhere-in-this-repo source of flaky "address already in
    # use" failures (see keycloak-bootstrap-login-client.sh's own
    # PORT_FORWARD_LOCAL_PORT comment for the same reasoning applied there).
    keycloak_login_local_port: int = 18444
