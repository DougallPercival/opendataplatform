"""platform-gateway's FastAPI app. Run locally with:

    uvicorn app.main:app --reload

against a reachable Keycloak + catalog-service (port-forwards for local
testing against a real cluster; see this package's README). Verifies every
caller's Keycloak JWT and proxies to catalog-service with gateway-derived
X-Workspace/X-User/X-Role headers — see app/auth.py and app/proxy.py for
the actual logic; this module just wires the two httpx clients they share
into app.state during startup and shuts them down cleanly on exit.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.config import settings
from app.jwks import JWKSCache
from app.modules import router as modules_router
from app.proxy import router as proxy_router


def _keycloak_tls_verify() -> str | bool:
    # A real Deployment always has this file mounted (gateway.yaml's volume,
    # sourced from platform-ca-secret — Reflector mirrors it into this
    # namespace, see argocd/manifests/cluster-issuer.yaml's secretTemplate
    # annotations). Falling back to the system default trust store (True)
    # rather than erroring when the file isn't there is what lets this
    # module import and the lifespan below construct cleanly in local
    # dev/tests, where nothing mounts it — real TLS verification of a real
    # in-cluster Keycloak connection only matters in-cluster, where the file
    # is always present.
    if Path(settings.keycloak_ca_path).is_file():
        return settings.keycloak_ca_path
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    catalog_client = httpx.AsyncClient(
        base_url=settings.catalog_service_url, timeout=settings.upstream_timeout_seconds
    )
    keycloak_client = httpx.AsyncClient(
        base_url=settings.keycloak_internal_url,
        timeout=settings.upstream_timeout_seconds,
        verify=_keycloak_tls_verify(),
    )
    app.state.catalog_client = catalog_client
    app.state.jwks = JWKSCache(
        client=keycloak_client, jwks_path=settings.jwks_path, ttl_seconds=settings.jwks_cache_seconds
    )
    try:
        yield
    finally:
        await catalog_client.aclose()
        await keycloak_client.aclose()


app = FastAPI(
    title="platform-gateway",
    description="Verifies Keycloak JWTs, proxies to catalog-service with derived, trustworthy "
    "X-Workspace/X-User/X-Role headers. ARCHITECTURE.md §2 (layer 3), §3, §10.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# modules_router (app/modules.py, platform-module-deps branch) has to be
# registered before proxy_router for the same reason /healthz is above it —
# proxy_router's `/{path:path}` catch-all would otherwise swallow
# /modules/check-requirements too. proxy_router stays LAST, after
# everything else, for that same reason. See proxy.py's own module
# docstring for the same point.
app.include_router(modules_router)
app.include_router(proxy_router)
