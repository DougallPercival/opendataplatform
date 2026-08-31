"""Integration test, real DB (see conftest.py). Datasets stand in for
pipelines/models too — they all go through the same app/crud.py, so one
API-level test suite per entity type would mostly be re-testing crud.py
three more times; datasets gets the full walk, the others get the plain-CRUD
smoke test in test_pipelines_and_models_smoke below.
"""
from __future__ import annotations


def _create_workspace(client, name: str) -> str:
    resp = client.post("/workspaces", json={"name": name, "display_name": name.title()})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_create_and_read_own_dataset(client):
    resp = client.post(
        "/datasets",
        headers={"X-Workspace": "personal", "X-User": "alice"},
        json={"name": "reddit-sentiment", "visibility": "private", "description": "raw pulls"},
    )
    assert resp.status_code == 201, resp.text
    dataset_id = resp.json()["id"]

    resp = client.get(f"/datasets/{dataset_id}", headers={"X-Workspace": "personal", "X-User": "alice"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "reddit-sentiment"


def test_private_dataset_hidden_from_other_workspace_public_is_not(client):
    other_id = _create_workspace(client, "other")

    private_resp = client.post(
        "/datasets",
        headers={"X-Workspace": "personal", "X-User": "alice"},
        json={"name": "private-one", "visibility": "private"},
    )
    public_resp = client.post(
        "/datasets",
        headers={"X-Workspace": "personal", "X-User": "alice"},
        json={"name": "public-one", "visibility": "public"},
    )
    private_id = private_resp.json()["id"]
    public_id = public_resp.json()["id"]

    # "other" workspace: private one is a 404 (not a 403 — see crud.py's
    # get_visible_or_404 docstring), public one is readable.
    resp = client.get(f"/datasets/{private_id}", headers={"X-Workspace": "other"})
    assert resp.status_code == 404

    resp = client.get(f"/datasets/{public_id}", headers={"X-Workspace": "other"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "public-one"

    # And "other" can't write it even though they can read it.
    resp = client.patch(
        f"/datasets/{public_id}", headers={"X-Workspace": "other"}, json={"description": "hijacked"}
    )
    assert resp.status_code == 403

    assert other_id  # workspace really was created, not just assumed


def test_duplicate_name_in_same_workspace_conflicts(client):
    body = {"name": "dupe", "visibility": "private"}
    first = client.post("/datasets", headers={"X-Workspace": "personal"}, json=body)
    assert first.status_code == 201

    second = client.post("/datasets", headers={"X-Workspace": "personal"}, json=body)
    assert second.status_code == 409


def test_pipelines_and_models_smoke(client):
    for path, body in (
        ("/pipelines", {"name": "nfl-weekly-pull", "visibility": "workspace"}),
        ("/models", {"name": "march-madness-bracket", "visibility": "private", "framework": "sklearn"}),
    ):
        created = client.post(path, headers={"X-Workspace": "personal"}, json=body)
        assert created.status_code == 201, created.text
        entity_id = created.json()["id"]

        listed = client.get(path, headers={"X-Workspace": "personal"})
        assert any(row["id"] == entity_id for row in listed.json())

        deleted = client.delete(f"{path}/{entity_id}", headers={"X-Workspace": "personal"})
        assert deleted.status_code == 204


def test_function_publish_bumps_version_and_promote_makes_it_public(client):
    created = client.post(
        "/functions",
        headers={"X-Workspace": "personal"},
        json={"name": "clean_text", "visibility": "private"},
    )
    function_id = created.json()["id"]
    assert created.json()["current_version"] == 0

    published = client.post(
        f"/functions/{function_id}/publish",
        headers={"X-Workspace": "personal"},
        json={
            "signature": "clean_text(s: str) -> str",
            "docstring": "Strips markdown and emoji.",
            "module_path": "platform_sdk.examples.clean_text",
        },
    )
    assert published.status_code == 201, published.text
    assert published.json()["version"] == 1

    refreshed = client.get(f"/functions/{function_id}", headers={"X-Workspace": "personal"})
    assert refreshed.json()["current_version"] == 1

    promoted = client.post(f"/functions/{function_id}/promote", headers={"X-Workspace": "personal"})
    assert promoted.status_code == 200
    assert promoted.json()["visibility"] == "public"

    # Now visible cross-workspace without ever joining "personal".
    other = client.post("/workspaces", json={"name": "another", "display_name": "Another"})
    assert other.status_code == 201
    cross = client.get(f"/functions/{function_id}", headers={"X-Workspace": "another"})
    assert cross.status_code == 200
