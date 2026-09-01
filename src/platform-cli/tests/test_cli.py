"""CLI-level tests — CliRunner drives `platform ...` exactly as a real
shell invocation would, but platform_cli.main.PlatformClient is
monkeypatched to a fake with no network/HTTP involved at all. That's a
deliberate second layer below platform-sdk's own respx-mocked tests: those
prove PlatformClient talks HTTP correctly, these prove the CLI wires
flags/output/errors around *a* client correctly — a fake stands in fine,
since PlatformClient's own behavior isn't what's under test here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from platform_sdk import (
    Dataset,
    InviteResult,
    KeycloakAdminError,
    PlatformAPIError,
    Principal,
    Role,
    Visibility,
    Workspace,
)
from typer.testing import CliRunner

import platform_cli.main as main_module
import platform_cli.workspace as workspace_module
from platform_cli.main import app

runner = CliRunner()


class FakeClient:
    """Stands in for PlatformClient. Records calls (for assertions) and
    returns canned/queued responses — no network, no real catalog-service."""

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.calls: list[tuple[str, tuple, dict]] = []
        self._responses: dict[str, object] = {}

    def queue(self, method: str, value) -> None:
        self._responses[method] = value

    def _record(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        result = self._responses.get(method)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        pass

    def me(self):
        return self._record("me")

    def list_workspaces(self):
        return self._record("list_workspaces") or []

    def create_workspace(self, name, display_name):
        return self._record("create_workspace", name, display_name)

    def get_workspace(self, workspace_id):
        return self._record("get_workspace", workspace_id)

    def list_datasets(self):
        return self._record("list_datasets") or []

    def create_dataset(self, name, *, visibility=Visibility.PRIVATE, description=None, location_uri=None):
        return self._record(
            "create_dataset", name, visibility=visibility, description=description, location_uri=location_uri
        )

    def get_dataset(self, dataset_id):
        return self._record("get_dataset", dataset_id)

    def update_dataset(self, dataset_id, **fields):
        return self._record("update_dataset", dataset_id, **fields)

    def delete_dataset(self, dataset_id):
        return self._record("delete_dataset", dataset_id)


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(main_module, "PlatformClient", lambda **kwargs: client)
    return client


class FakeKeycloakAdminClient:
    """Stands in for KeycloakAdminClient — no real port-forward/kubectl/HTTP
    involved. `invite` is the only command that uses this, and it's the one
    command in this file NOT covered by platform-sdk's own respx-mocked
    tests for KeycloakAdminClient (that suite proves the Admin API calls are
    right; this one proves the CLI wires flags/output/errors around *a*
    client correctly — same split as FakeClient/PlatformClient above)."""

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.invite_calls: list[tuple] = []
        self._invite_response: InviteResult | Exception | None = None

    def queue_invite(self, value) -> None:
        self._invite_response = value

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def invite(self, username, *, workspace, role):
        self.invite_calls.append((username, workspace, role))
        if isinstance(self._invite_response, Exception):
            raise self._invite_response
        return self._invite_response


@pytest.fixture
def fake_keycloak(monkeypatch):
    client = FakeKeycloakAdminClient()
    monkeypatch.setattr(workspace_module, "KeycloakAdminClient", lambda **kwargs: client)
    return client


def _workspace(name="personal") -> Workspace:
    return Workspace(id=uuid.uuid4(), name=name, display_name=name.title(), created_at=datetime.now(UTC))


def _dataset(name="reddit-sentiment", **overrides) -> Dataset:
    fields = dict(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name=name,
        visibility=Visibility.PRIVATE,
        description=None,
        location_uri=None,
        created_by="alice",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    fields.update(overrides)
    return Dataset(**fields)


def test_me_prints_principal(fake_client):
    principal = Principal(
        workspace_id=uuid.uuid4(), workspace_name="personal", user_id="alice", role="owner"
    )
    fake_client.queue("me", principal)
    result = runner.invoke(app, ["me"])
    assert result.exit_code == 0, result.output
    assert "personal" in result.output
    assert "alice" in result.output
    assert "owner" in result.output


def test_workspace_list_empty_says_so(fake_client):
    result = runner.invoke(app, ["workspace", "list"])
    assert result.exit_code == 0
    assert "No workspaces" in result.output


def test_workspace_create_defaults_display_name_to_title_case(fake_client):
    fake_client.queue("create_workspace", _workspace("nfl-betting"))
    result = runner.invoke(app, ["workspace", "create", "nfl-betting"])
    assert result.exit_code == 0, result.output
    method, args, kwargs = fake_client.calls[-1]
    assert method == "create_workspace"
    assert args == ("nfl-betting", "Nfl-Betting")  # str.title() — good enough for this pass, see README


def test_dataset_create_passes_visibility_case_insensitively(fake_client):
    fake_client.queue("create_dataset", _dataset("public-thing", visibility=Visibility.PUBLIC))
    result = runner.invoke(app, ["dataset", "create", "public-thing", "--visibility", "PUBLIC"])
    assert result.exit_code == 0, result.output
    method, args, kwargs = fake_client.calls[-1]
    assert kwargs["visibility"] == Visibility.PUBLIC


def test_dataset_update_with_no_flags_exits_nonzero_without_calling_client(fake_client):
    result = runner.invoke(app, ["dataset", "update", str(uuid.uuid4())])
    assert result.exit_code == 1
    assert fake_client.calls == []  # never even reached the client


def test_dataset_update_only_sends_the_flags_actually_passed(fake_client):
    dataset_id = str(uuid.uuid4())
    fake_client.queue("update_dataset", _dataset(description="new desc"))
    result = runner.invoke(app, ["dataset", "update", dataset_id, "--description", "new desc"])
    assert result.exit_code == 0, result.output
    method, args, kwargs = fake_client.calls[-1]
    assert args == (dataset_id,)
    assert kwargs == {"description": "new desc"}  # visibility/location_uri NOT present, not None-valued


def test_api_error_prints_to_stderr_and_exits_1(fake_client):
    error = PlatformAPIError(404, "Dataset not found.", method="GET", url="/datasets/x")
    fake_client.queue("get_dataset", error)
    result = runner.invoke(app, ["dataset", "get", "some-id"])
    assert result.exit_code == 1
    assert "404" in result.output
    assert "Dataset not found." in result.output


def test_workspace_invite_defaults_role_to_viewer_and_workspace_to_setting(fake_keycloak, monkeypatch):
    # Confirms the "personal" fallback rather than an env leak from another test.
    monkeypatch.delenv("PLATFORM_WORKSPACE", raising=False)
    fake_keycloak.queue_invite(
        InviteResult(
            username="alice", workspace="personal", role=Role.VIEWER,
            group_path="/workspaces/personal/viewer", group_created=False,
        )
    )
    result = runner.invoke(app, ["workspace", "invite", "alice"])
    assert result.exit_code == 0, result.output
    username, workspace, role = fake_keycloak.invite_calls[-1]
    assert (username, workspace, role) == ("alice", "personal", Role.VIEWER)
    assert "Reused" in result.output
    assert "/workspaces/personal/viewer" in result.output


def test_workspace_invite_passes_workspace_and_role_flags(fake_keycloak):
    fake_keycloak.queue_invite(
        InviteResult(
            username="bob", workspace="nfl-betting", role=Role.EDITOR,
            group_path="/workspaces/nfl-betting/editor", group_created=True,
        )
    )
    result = runner.invoke(
        app, ["workspace", "invite", "bob", "--workspace", "nfl-betting", "--role", "editor"]
    )
    assert result.exit_code == 0, result.output
    assert fake_keycloak.invite_calls[-1] == ("bob", "nfl-betting", Role.EDITOR)
    assert "Created" in result.output


def test_workspace_invite_keycloak_error_prints_and_exits_1(fake_keycloak):
    fake_keycloak.queue_invite(KeycloakAdminError("No Keycloak user named 'ghost' in realm 'platform'."))
    result = runner.invoke(app, ["workspace", "invite", "ghost"])
    assert result.exit_code == 1
    assert "ghost" in result.output
