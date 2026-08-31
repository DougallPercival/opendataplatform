"""Pure logic tests for app/visibility.py — no DB needed, since can_read/
can_write only look at plain attributes. The one thing worth locking down
with tests before anything else builds on it: ARCHITECTURE.md §4's "public
means readable by everyone else, not writable" asymmetry.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.models import Visibility
from app.visibility import Principal, can_read, can_write

WORKSPACE_A = uuid.uuid4()
WORKSPACE_B = uuid.uuid4()


@dataclass
class FakeEntity:
    workspace_id: uuid.UUID
    visibility: Visibility
    created_by: str | None


def _principal(workspace_id: uuid.UUID, user_id: str = "alice") -> Principal:
    return Principal(workspace_id=workspace_id, workspace_name="doesn't matter here", user_id=user_id)


def test_public_readable_by_anyone_but_not_writable_outside_owning_workspace():
    entity = FakeEntity(workspace_id=WORKSPACE_A, visibility=Visibility.PUBLIC, created_by="alice")
    outsider = _principal(WORKSPACE_B)

    assert can_read(outsider, entity) is True
    assert can_write(outsider, entity) is False


def test_workspace_visible_to_any_member_not_just_creator():
    entity = FakeEntity(workspace_id=WORKSPACE_A, visibility=Visibility.WORKSPACE, created_by="alice")
    teammate = _principal(WORKSPACE_A, user_id="bob")

    assert can_read(teammate, entity) is True
    assert can_write(teammate, entity) is True


def test_private_visible_only_to_its_creator_even_within_the_same_workspace():
    entity = FakeEntity(workspace_id=WORKSPACE_A, visibility=Visibility.PRIVATE, created_by="alice")
    teammate = _principal(WORKSPACE_A, user_id="bob")
    creator = _principal(WORKSPACE_A, user_id="alice")

    assert can_read(teammate, entity) is False
    assert can_read(creator, entity) is True
    assert can_write(teammate, entity) is False
    assert can_write(creator, entity) is True


def test_nothing_visible_or_writable_from_an_unrelated_workspace_unless_public():
    outsider = _principal(WORKSPACE_B)
    for visibility in (Visibility.PRIVATE, Visibility.WORKSPACE):
        entity = FakeEntity(workspace_id=WORKSPACE_A, visibility=visibility, created_by="alice")
        assert can_read(outsider, entity) is False
        assert can_write(outsider, entity) is False
