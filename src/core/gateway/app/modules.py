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

`GET /modules` — ui-shell-plan.md item 4 (feature/gateway-module-registry
branch, 2026-09-08): lists installed modules with the displayName/icon/
navPath ui-shell's future nav needs (item 5, still deferred — blocked on
item 3, browser OAuth). Deliberately installed-only, no static release-time
catalog (that's item 6, bigger and separable) — same "the caller already has
what it needs locally" move this file's check-requirements already made.
Reuses `require_auth` exactly like check-requirements above, following that
same precedent rather than inventing a workspace-optional auth variant: a
verified user's own membership check gates this the same way it gates
everything else behind gateway, even though module Applications themselves
aren't workspace-scoped resources.

`GET /modules/catalog` — ui-shell-plan.md item 6 (feature/gateway-module-catalog branch,
2026-09-09): the Add-ons page's static release-time catalog `GET /modules` above deliberately left
out — every module under `src/modules/`, installed or not, overlaid with live status. Reads
`app/module_index.py`'s `load_static_module_index()` (the JSON `platform module build-index`
generates at gateway's own build time — see that module's docstring) for display metadata
(`display_name`/`icon`/`nav_path`/`requires`/`optional`), then `list_module_applications()` — the
same narrow name->health dict `check-requirements` already uses, not `list_module_summaries()` —
for live status only, defaulting to `_NOT_INSTALLED_STATUS` exactly like `check-requirements`
already does. Display metadata deliberately always comes from the static file, never from a live
Application's `platform.io/*` annotations: ARCHITECTURE.md §3 scopes the overlay to *state* only
("overlays it with... live registrations... shows each module's state"), and the static file
(regenerated every gateway release) can't go stale relative to `module.yaml` the way a long-installed
module's un-reinstalled Application annotations can. `requires`/`optional` are included even though
nothing here computes satisfaction from them — a future Install button (item 7) needs exactly this
to show a disabled state with why, and `check-requirements` above already owns that computation
("the dependency check lives once, at the API layer both doors call through" — this endpoint only
ever passes the raw list through).
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.argocd import ArgoCDUnavailableError, list_module_applications, list_module_summaries
from app.auth import AuthError, require_auth
from app.jwks import JWKSCache
from app.module_index import load_static_module_index

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


@router.get("/modules")
async def list_modules(
    request: Request,
    authorization: str | None = Header(default=None),
    x_workspace: str | None = Header(default=None),
):
    jwks: JWKSCache = request.app.state.jwks
    try:
        await require_auth(authorization, x_workspace, jwks)
    except AuthError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    try:
        modules = await list_module_summaries()
    except ArgoCDUnavailableError as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    # Every installed module regardless of status (Healthy/Progressing/
    # Degraded/Unknown), not filtered to healthy-only — how to render a
    # degraded module (grey it out, badge it, hide it) is ui-shell's call
    # once it builds real nav (item 5), not something this endpoint should
    # pre-decide by withholding data.
    return {
        "modules": [
            {
                "module_id": m.module_id,
                "display_name": m.display_name,
                "icon": m.icon,
                "nav_path": m.nav_path,
                "status": m.status,
            }
            for m in modules
        ]
    }


@router.get("/modules/catalog")
async def list_module_catalog(
    request: Request,
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

    # A missing/malformed static index degrades to [] (see module_index.py's own docstring) rather
    # than a second error path here — an empty catalog on its own is never a 503, only Argo CD
    # unreachability above is.
    static_modules = load_static_module_index()
    return {
        "modules": [
            {
                "module_id": m["id"],
                "display_name": m["displayName"],
                "icon": m["icon"],
                "nav_path": m["navPath"],
                "requires": m["requires"],
                "optional": m["optional"],
                "status": installed.get(m["id"], _NOT_INSTALLED_STATUS),
            }
            for m in static_modules
        ]
    }
