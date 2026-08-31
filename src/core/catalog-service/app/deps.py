"""FastAPI dependencies — currently just one, and it's a placeholder.

get_current_principal reads two plain headers instead of verifying a
Keycloak JWT. That's deliberate for THIS step (ARCHITECTURE.md's Phase 2
kickoff is "catalog-lite's data model first" — gateway/auth integration is
its own piece of work, not yet built: see ../gateway/README.md). Real auth
lands when platform-gateway starts proxying requests here — it's the
natural place to verify the JWT once (already true for every other module
it proxies to, per ARCHITECTURE.md §3) and forward the verified identity
downstream as trusted headers, the same shape this already expects. Nothing
in visibility.py or the routers needs to change when that happens — only
this function.

Until then: no verification, anyone can claim to be anyone. Fine for local
dev and for exercising the schema; NOT fine to expose this service directly
past a cluster boundary before gateway sits in front of it. Tracked in
docs/known-issues.md.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Workspace
from app.visibility import Principal

DEFAULT_WORKSPACE = "personal"
DEFAULT_USER = "anonymous"


def get_current_principal(
    x_workspace: str | None = Header(default=None, alias="X-Workspace"),
    x_user: str | None = Header(default=None, alias="X-User"),
    db: Session = Depends(get_db),
) -> Principal:
    workspace_name = x_workspace or DEFAULT_WORKSPACE
    workspace = db.query(Workspace).filter(Workspace.name == workspace_name).one_or_none()
    if workspace is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown workspace '{workspace_name}' (X-Workspace header, or the "
            f"'{DEFAULT_WORKSPACE}' default) — it needs a row in the workspaces table first.",
        )
    return Principal(
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        user_id=x_user or DEFAULT_USER,
    )
