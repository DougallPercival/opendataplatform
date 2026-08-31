"""The one place the read/write rule from ARCHITECTURE.md §4 is implemented
— every router calls into this rather than re-deriving it, so "who can see
what" has exactly one definition in this codebase.

Rule (matches models.py's Visibility docstring):
  read  — public: anyone. workspace: any member of the owning workspace.
          private: only the entity's own creator.
  write — ARCHITECTURE.md §4: "Editing rights stay with the owning workspace
          regardless of visibility: 'public' means readable by everyone
          else, not writable." So write is read's rule minus the "public
          means anyone" case — always gated on owning-workspace membership.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_

from app.models import Visibility


@dataclass(frozen=True)
class Principal:
    """The requester, as resolved by app/deps.py. See that module's
    docstring for how this is currently a placeholder, not real auth."""

    workspace_id: UUID
    workspace_name: str
    user_id: str


class _VisibleEntity(Protocol):
    workspace_id: UUID
    visibility: Visibility
    created_by: str | None


def can_read(principal: Principal, entity: _VisibleEntity) -> bool:
    if entity.visibility == Visibility.PUBLIC:
        return True
    if entity.workspace_id != principal.workspace_id:
        return False
    if entity.visibility == Visibility.WORKSPACE:
        return True
    # PRIVATE: only the creator, even within the owning workspace.
    return entity.created_by == principal.user_id


def can_write(principal: Principal, entity: _VisibleEntity) -> bool:
    # Same "must be in the owning workspace" gate as read's workspace/private
    # cases — write never gets the "public means anyone" carve-out.
    if entity.workspace_id != principal.workspace_id:
        return False
    if entity.visibility == Visibility.PRIVATE:
        return entity.created_by == principal.user_id
    return True


def read_filter(principal: Principal, model) -> ColumnElement[bool]:
    """The same rule as can_read, expressed as a SQLAlchemy filter for list
    endpoints — so listing datasets/functions/pipelines/models doesn't mean
    "load everything, then filter in Python."
    """
    return or_(
        model.visibility == Visibility.PUBLIC,
        and_(
            model.workspace_id == principal.workspace_id,
            or_(
                model.visibility == Visibility.WORKSPACE,
                and_(model.visibility == Visibility.PRIVATE, model.created_by == principal.user_id),
            ),
        ),
    )
