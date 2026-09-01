"""Unit tests for PlatformClient — mocked at the HTTP transport level via
respx, not run against a live catalog-service. Deliberate: this package's
job is "does PlatformClient send the right request and parse the right
response," not "does catalog-service work" (that's catalog-service's own
test suite, against a real Postgres, over in src/core/catalog-service/tests/).
Keeping this SDK's tests transport-mocked means its CI job never needs a
Postgres service container the way catalog-service's does — a real
dependency-shape difference between a service and a client library, not
laziness.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from platform_sdk import PlatformAPIError, PlatformClient, Visibility

BASE_URL = "http://catalog.test"


@pytest.fixture
def client():
    c = PlatformClient(catalog_url=BASE_URL, workspace="personal", user="alice")
    yield c
    c.close()


def _workspace_body(name: str = "personal") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "display_name": name.title(),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _dataset_body(name: str = "reddit-sentiment", **overrides) -> dict:
    body = {
        "id": str(uuid.uuid4()),
        "workspace_id": str(uuid.uuid4()),
        "name": name,
        "visibility": "private",
        "description": None,
        "location_uri": None,
        "created_by": "alice",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    body.update(overrides)
    return body


@respx.mock
def test_headers_send_workspace_and_user_but_omit_role_by_default(client):
    route = respx.get(f"{BASE_URL}/me").mock(
        return_value=httpx.Response(
            200, json={"workspace_id": str(uuid.uuid4()), "workspace_name": "personal",
                       "user_id": "alice", "role": "owner"}
        )
    )
    client.me()
    sent = route.calls.last.request
    assert sent.headers["x-workspace"] == "personal"
    assert sent.headers["x-user"] == "alice"
    assert "x-role" not in sent.headers  # unset role -> header omitted, see client.py's docstring


@respx.mock
def test_role_header_sent_when_set():
    c = PlatformClient(catalog_url=BASE_URL, workspace="personal", user="bob", role="viewer")
    route = respx.get(f"{BASE_URL}/me").mock(
        return_value=httpx.Response(
            200, json={"workspace_id": str(uuid.uuid4()), "workspace_name": "personal",
                       "user_id": "bob", "role": "viewer"}
        )
    )
    principal = c.me()
    assert route.calls.last.request.headers["x-role"] == "viewer"
    assert principal.role == "viewer"
    c.close()


@respx.mock
def test_list_and_create_workspace(client):
    respx.get(f"{BASE_URL}/workspaces").mock(
        return_value=httpx.Response(200, json=[_workspace_body("personal")])
    )
    workspaces = client.list_workspaces()
    assert len(workspaces) == 1
    assert workspaces[0].name == "personal"

    respx.post(f"{BASE_URL}/workspaces").mock(
        return_value=httpx.Response(201, json=_workspace_body("another"))
    )
    created = client.create_workspace("another", "Another")
    assert created.name == "another"


@respx.mock
def test_dataset_crud_round_trip(client):
    dataset_id = str(uuid.uuid4())

    respx.post(f"{BASE_URL}/datasets").mock(
        return_value=httpx.Response(201, json=_dataset_body(id=dataset_id))
    )
    created = client.create_dataset("reddit-sentiment", visibility=Visibility.PRIVATE)
    assert str(created.id) == dataset_id
    assert created.visibility is Visibility.PRIVATE

    respx.get(f"{BASE_URL}/datasets/{dataset_id}").mock(
        return_value=httpx.Response(200, json=_dataset_body(id=dataset_id))
    )
    fetched = client.get_dataset(dataset_id)
    assert str(fetched.id) == dataset_id

    respx.patch(f"{BASE_URL}/datasets/{dataset_id}").mock(
        return_value=httpx.Response(200, json=_dataset_body(id=dataset_id, description="updated"))
    )
    updated = client.update_dataset(dataset_id, description="updated")
    assert updated.description == "updated"

    respx.delete(f"{BASE_URL}/datasets/{dataset_id}").mock(return_value=httpx.Response(204))
    client.delete_dataset(dataset_id)  # no exception = success


@respx.mock
def test_404_surfaces_as_platform_api_error_with_status_and_detail(client):
    missing_id = str(uuid.uuid4())
    respx.get(f"{BASE_URL}/datasets/{missing_id}").mock(
        return_value=httpx.Response(404, json={"detail": "Dataset not found."})
    )
    with pytest.raises(PlatformAPIError) as exc_info:
        client.get_dataset(missing_id)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Dataset not found."


@respx.mock
def test_403_from_viewer_role_surfaces_correctly():
    c = PlatformClient(catalog_url=BASE_URL, workspace="personal", user="bob", role="viewer")
    respx.post(f"{BASE_URL}/datasets").mock(
        return_value=httpx.Response(403, json={"detail": "Viewers cannot create entries."})
    )
    with pytest.raises(PlatformAPIError) as exc_info:
        c.create_dataset("nope")
    assert exc_info.value.status_code == 403
    c.close()


def test_client_is_a_context_manager():
    with PlatformClient(catalog_url=BASE_URL) as c:
        assert c is not None
    # __exit__ closed the underlying httpx.Client — a second close() is a
    # harmless no-op (httpx's own contract), just confirming this doesn't
    # raise rather than asserting anything deeper about httpx's internals.
    c.close()
