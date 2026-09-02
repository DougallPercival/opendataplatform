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
hold (multiple named catalog-service targets, saved credentials once real
auth exists) — add it then, not speculatively now.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class SDKSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLATFORM_", env_file=".env", env_file_encoding="utf-8")

    # catalog-service's own default local address (see that service's
    # README's "Running locally") — matches without either side needing to
    # know about the other's defaults by coincidence, just by both picking
    # the obvious one.
    catalog_url: str = "http://localhost:8000"
    workspace: str = "personal"
    # None, not a hardcoded default, on purpose: PlatformClient falls back
    # to getpass.getuser() (the actual OS user running the CLI) rather than
    # a placeholder string, so `platform dataset create ...` run by two
    # different people on the same box records two different created_by
    # values without either of them setting anything.
    user: str | None = None
    # None, not "owner", on purpose too: leaving this unset means the
    # X-Role header is omitted entirely, and catalog-service's own
    # DEFAULT_ROLE (owner) applies server-side — one definition of "what
    # role means to run as if you didn't say," not two that could drift
    # apart. Only set PLATFORM_ROLE when you deliberately want to exercise
    # (or restrict yourself to) a specific role, e.g. testing the CLI as a
    # viewer.
    role: str | None = None

    # --- Keycloak Admin API (platform_sdk.keycloak_admin, `platform
    # workspace invite`) --- Separate from catalog_url/workspace/user/role
    # above: this talks to Keycloak directly, not through catalog-service
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
