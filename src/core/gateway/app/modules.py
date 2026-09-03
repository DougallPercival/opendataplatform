"""`GET /modules/check-requirements` — module-lifecycle-plan.md item 6
(platform-module-deps branch, 2026-09-03). ARCHITECTURE.md §3: "the
dependency check lives once, at the API layer both doors call through" —
this is that one place. `platform module install` (platform-cli) is the
first caller; a future Add-ons page (item 7, still deferred) would call the
exact same endpoint with a module's own `requires` list, not a second
implementation of the satisfied/not-satisfied comparison.

Requires the same auth proxy.py's catch-all enforces (a verified token,
member of the workspace named by X-Workspace) — dependency information is
scoped the same way everything else behind gateway is, even though
argocd.py's own query isn't workspace-scoped itself (Argo CD Applications
aren't workspace-scoped resources); this endpoint doesn't leak anything an
authenticated platform-cli user couldn't already infer by attempting the
install and reading Argo CD's own error, it just answers faster and without
a wasted failed install.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.argocd import ArgoCDUnavailableError, list_module_applications
from app.auth import AuthError, require_auth
from app.jwks import JWKSCache

router = APIRouter()

# A module is usable by something that depends on it only once Argo CD
# reports it Healthy — Progressing/Degraded/Missing/Unknown are all "not
# satisfied," not just "absent." See this branch's plan, decision 2: a
# dependency that isn't actually up yet isn't a dependency that's met.
_SATISFIED_STATUS = "Healthy"
_NOT_INSTALLED_STATUS = "not installed"


@router.get("/modules/check-requirements")
async def check_requirements(
    request: Request,
    # Repeated query params (?requires=a&requires=b), per this branch's plan
    # — matches how a module's own `requires: [...]` list (already a plain
    # list) gets forwarded by platform-sdk's check_module_requirements
    # without needing to invent a delimiter/encoding for a single string.
    requires: list[str] = Query(default=[]),
    authorization: str | None = Header(default=None),
    x_workspace: str | None = Header(default=None),
):
    jwks: JWKSCache = request.app.state.jwks
    try:
        await require_auth(authorization, x_workspace, jwks)
    except AuthError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    try:
        installed = await list_module_applications()
    except ArgoCDUnavailableError as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    results = []
    for module_id in requires:
        status = installed.get(module_id, _NOT_INSTALLED_STATUS)
        results.append(
            {"module_id": module_id, "satisfied": status == _SATISFIED_STATUS, "status": status}
        )
    return {"results": results}
