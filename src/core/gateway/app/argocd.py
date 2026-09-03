"""In-cluster Kubernetes API client for reading Argo CD `Application` health —
platform-module-deps branch (module-lifecycle-plan.md item 6, 2026-09-03).

`app/modules.py`'s `/modules/check-requirements` endpoint needs to know which
modules are currently installed-and-healthy. Every module Application
`platform module install` writes (platform_cli/manifest.py's
render_application_manifest, platform-module-lifecycle branch) carries
`metadata.name: <module id>` and `metadata.labels: {platform.io/tier:
module}` — so "is module X up" is answerable as a single Kubernetes API list
call against Argo CD's own CRD, no separate module registry needed.

Deliberately raw `httpx` rather than the official `kubernetes` package: this
package already talks to every other backend (catalog-service, Keycloak)
through httpx.AsyncClient, and pulling in a heavyweight, sync-first client
library for one read-only, one-shot GET would be a real departure from that
pattern (see this branch's plan, decision 3) — `asyncio.to_thread`-wrapping
a sync library call is more moving parts than this needs.

Auth: the bearer token is read fresh from the mounted ServiceAccount token
file on EVERY call, not cached — it's a cheap local file read (no network),
it's always current (kubelet rotates the projected token well before expiry;
re-reading the file picks that up for free), and caching it would just be
unnecessary complexity for no real benefit here. TLS is verified against the
in-cluster CA bundle the same ServiceAccount projection mounts.

Same "file exists -> real path, else dev-friendly fallback" shape
main.py's `_keycloak_tls_verify()` already uses: outside a real Deployment
(local dev, unit tests) these files aren't mounted, so `list_module_applications`
raises `ArgoCDUnavailableError` up front instead of crashing on a
FileNotFoundError deep inside an httpx call — `app/modules.py` turns that into
a 503, a legible "can't check requirements right now" rather than a 500.
"""
from __future__ import annotations

import ssl
from pathlib import Path

import httpx

from app.config import settings

# Every module Application platform_cli/manifest.py's render_application_manifest
# writes carries this label — see that module's docstring and this branch's
# plan, decision 2. Anything else living in the argocd namespace (root,
# modules-root, core service Applications) does NOT carry it, so this
# selector is what keeps this query scoped to modules only.
_MODULE_TIER_LABEL = "platform.io/tier=module"


class ArgoCDUnavailableError(Exception):
    """Raised for anything that stops this from getting a real answer from
    the Kubernetes API: the ServiceAccount token/CA aren't mounted (not
    running in-cluster, or RBAC/volume misconfigured), a connection failure,
    a non-2xx response (a 403 here means the Role/RoleBinding this branch
    adds is wrong or missing — see argocd/README.md), or a malformed body.

    Deliberately its own exception, not silently treated as "no modules
    installed": that would make every dependency look unsatisfied for a
    plumbing reason having nothing to do with whether the dependency is
    actually there, which is a much worse failure mode than a clear error.
    """


def _read_text(path: str, *, what: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise ArgoCDUnavailableError(
            f"{what} not found at {path!r} — not running in-cluster, or the gateway "
            "ServiceAccount isn't mounted (see argocd/manifests/gateway.yaml)."
        )
    return p.read_text().strip()


async def list_module_applications() -> dict[str, str]:
    """Returns `{module_id: health_status}` for every Argo CD Application
    labeled `platform.io/tier=module` in `settings.argocd_namespace` — one
    entry per module that has ever been `platform module install`ed,
    regardless of its current health (a module can be `Progressing` or
    `Degraded`, not just `Healthy`; app/modules.py decides what counts as
    "satisfied", this function just reports what's really there). A module
    that was never installed simply has no key in the returned dict —
    callers check with `.get(module_id)`, not a KeyError.
    """
    token = _read_text(settings.k8s_sa_token_path, what="ServiceAccount token")
    ca_path = settings.k8s_sa_ca_path
    if not Path(ca_path).is_file():
        raise ArgoCDUnavailableError(
            f"ServiceAccount CA bundle not found at {ca_path!r} — not running in-cluster, or the "
            "gateway ServiceAccount isn't mounted (see argocd/manifests/gateway.yaml)."
        )

    url = (
        f"{settings.k8s_api_url}/apis/argoproj.io/v1alpha1/namespaces/"
        f"{settings.argocd_namespace}/applications"
    )
    # httpx>=0.28 deprecated passing `verify=<path-string>` directly (it still
    # works, just warns) in favor of building the SSLContext explicitly —
    # doing that here rather than leaving the warning in every test run.
    ssl_context = ssl.create_default_context(cafile=ca_path)
    try:
        async with httpx.AsyncClient(verify=ssl_context, timeout=settings.upstream_timeout_seconds) as client:
            response = await client.get(
                url,
                params={"labelSelector": _MODULE_TIER_LABEL},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise ArgoCDUnavailableError(f"Couldn't reach the Kubernetes API at {url!r}: {exc}") from exc

    if response.status_code != 200:
        raise ArgoCDUnavailableError(
            f"Kubernetes API returned {response.status_code} listing Applications "
            f"(labelSelector={_MODULE_TIER_LABEL!r}, namespace={settings.argocd_namespace!r}): "
            f"{response.text[:500]}"
        )

    try:
        body = response.json()
        items = body["items"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ArgoCDUnavailableError(f"Unexpected response shape from the Kubernetes API: {exc}") from exc

    result: dict[str, str] = {}
    for item in items:
        name = item.get("metadata", {}).get("name")
        if not name:
            continue
        # Absent entirely (e.g. Argo hasn't run a health check yet, right
        # after the Application was created) reads as "Unknown" — a real,
        # distinct-from-Healthy status app/modules.py reports as unsatisfied,
        # rather than this function guessing or defaulting to "Healthy".
        health_status = item.get("status", {}).get("health", {}).get("status") or "Unknown"
        result[name] = health_status
    return result
