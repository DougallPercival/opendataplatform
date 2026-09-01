"""Typed shapes for what catalog-service's JSON responses actually look
like. Deliberately NOT imported from app.schemas/app.models over in
catalog-service — that package is a service's internals, not a published
library, and importing it would mean platform-sdk (installed wherever code
using @platform.dataset runs, per ARCHITECTURE.md §4) needing catalog-service
importable too, defeating the point of talking to it over HTTP instead. A
little duplication of field shapes here, against catalog-service's public
REST contract rather than its Python internals, is the actual decoupling —
same tradeoff models.py's own `_pg_enum` comment makes elsewhere in this
repo, applied one layer up.

Only Workspace, Dataset, and Principal exist yet — the minimal slice this
pass covers. Pipeline/MLModel/Function/FunctionVersion/LineageEdge are the
same shape of work again once platform-cli actually needs them (see this
package's README).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel


class Visibility(enum.StrEnum):
    """Mirrors app.models.Visibility over in catalog-service exactly (same
    three lowercase values) — this is the public contract those values are
    part of, not an internal detail, so duplicating it here (rather than
    importing it) is the point, not a shortcut. See this module's docstring."""

    PRIVATE = "private"
    WORKSPACE = "workspace"
    PUBLIC = "public"


class Workspace(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    created_at: datetime


class Dataset(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    visibility: Visibility
    description: str | None
    location_uri: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class Principal(BaseModel):
    """Body of GET /me — see catalog-service's PrincipalRead docstring for
    why this is a "who does the server think I am" snapshot, not a
    Workspace. Role is plain str, not an enum, here: the SDK doesn't
    enforce or branch on role itself (catalog-service already does, and
    returns a 403 platform-sdk surfaces as PlatformAPIError) — this is just
    what came back."""

    workspace_id: uuid.UUID
    workspace_name: str
    user_id: str
    role: str
