"""Settings, read from environment / .env — same pydantic-settings pattern
catalog-service/app/config.py and platform_sdk/config.py both use. See
.env.example for the in-cluster values gateway.yaml's Deployment actually
sets; the defaults below match those, so a plain `docker run` against a
real cluster's Keycloak/catalog-service (e.g. via port-forwards for local
testing) needs little to no overriding.

Two Keycloak URLs, deliberately different strings pointing at the same
Keycloak — this is the single most important thing to get right in this
file, so it's explained here rather than left for someone to work out from
a confusing bug report:

- `keycloak_internal_url` — where gateway itself CONNECTS to fetch the JWKS
  (jwks.py). Keycloak's real in-cluster Service DNS name, reached directly
  — no port-forward, no hostname-resolution patch. platform_sdk's CLI-side
  Keycloak tooling needs that trick (see
  platform_sdk/_keycloak_connection.py's docstring) because Keycloak's
  hostname provider strictly enforces `keycloak.platform.local` for
  everything it GENERATES; that's a separate concern from what Host header
  a request arrives with, and pure JWKS/JSON API calls never hit it. What
  DOES need fixing for this URL to work is TLS: `keycloak-tls`'s Certificate
  needed this DNS name added as a SAN (see
  argocd/manifests/keycloak-instance.yaml, 2026-09-02) since a cert only
  listing the public hostname would otherwise fail hostname verification
  for a direct in-cluster connection.
- `keycloak_public_url` — what Keycloak actually PUTS in every token's
  `iss` claim. Governed by `spec.hostname.hostname`
  (keycloak.platform.local) — that setting affects every URL Keycloak
  itself *generates*, issuer strings included, regardless of which
  hostname/IP a client actually connected through. verify_token() (auth.py)
  checks a token's `iss` against THIS value, never against
  `keycloak_internal_url` — using the wrong one here would make every
  single token fail verification with a confusing issuer-mismatch error,
  not a connection error, which is exactly the kind of thing worth a long
  comment to prevent.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", env_file_encoding="utf-8")

    keycloak_realm: str = "platform"
    keycloak_internal_url: str = "https://platform-service.keycloak.svc.cluster.local:8443"
    keycloak_public_url: str = "https://keycloak.platform.local"
    # Mounted from platform-ca-secret (cert-manager's platform-ca
    # ClusterIssuer) — Reflector mirrors that Secret into this service's
    # namespace (see argocd/manifests/cluster-issuer.yaml's secretTemplate
    # annotations, 2026-09-02) and gateway.yaml's Deployment mounts it here.
    # Falls back to the system default trust store if this path doesn't
    # exist (see main.py's _keycloak_tls_verify()) — true in local dev/tests,
    # never true in a real Deployment.
    keycloak_ca_path: str = "/etc/gateway/keycloak-ca/ca.crt"

    # catalog-service's real in-cluster Service DNS name + port — see
    # argocd/manifests/catalog-service.yaml's Service (name/namespace both
    # "catalog-service", port 8000).
    catalog_service_url: str = "http://catalog-service.catalog-service.svc.cluster.local:8000"

    # How long a fetched JWKS document is trusted before get_key() forces a
    # refetch even for a `kid` it already has cached — bounds how long a
    # revoked/rotated Keycloak signing key could theoretically still verify
    # a token here (it can't actually forge a NEW token without Keycloak's
    # private key, but this bounds how long an already-issued token signed
    # with a since-rotated key stays accepted). 5 minutes is generous
    # relative to how rarely Keycloak actually rotates realm keys.
    jwks_cache_seconds: int = 300

    upstream_timeout_seconds: float = 10.0

    # platform-module-deps branch (2026-09-03) — app/argocd.py's in-cluster
    # Kubernetes API client, for GET /modules/check-requirements
    # (app/modules.py). Standard mounted-ServiceAccount paths/URL; a real
    # Deployment always has these (see gateway.yaml's new `gateway`
    # ServiceAccount), same "file exists -> real value, else dev-friendly
    # gap" story as keycloak_ca_path above — see argocd.py's own docstring
    # for what happens when they're missing (a clear 503, not a crash).
    k8s_api_url: str = "https://kubernetes.default.svc"
    k8s_sa_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    k8s_sa_ca_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    # Namespace argocd/manifests/*.yaml install every Application into
    # (root/apps/core/modules-root/each modules-enabled/*.yaml) — see
    # argocd/README.md.
    argocd_namespace: str = "argocd"

    @property
    def jwks_path(self) -> str:
        return f"/realms/{self.keycloak_realm}/protocol/openid-connect/certs"

    @property
    def expected_issuer(self) -> str:
        return f"{self.keycloak_public_url}/realms/{self.keycloak_realm}"


settings = Settings()
