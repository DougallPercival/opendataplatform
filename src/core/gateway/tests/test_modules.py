"""Integration tests for GET /modules/check-requirements and GET /modules,
exercised through FastAPI's TestClient — same shape as test_proxy.py: the
real ASGI app, real lifespan, respx intercepting the Keycloak JWKS endpoint
and (here) the Kubernetes API `mounted_sa` points at. No live cluster
needed; auth-failure cases mirror test_proxy.py's exactly, since
app.auth.require_auth wraps the same verify_token/derive_headers proxy.py's
catch-all uses.

GET /modules (feature/gateway-module-registry, 2026-09-08, ui-shell-plan.md
item 4) shares the same auth and Kubernetes-mocking helpers below —
`_application()` grew an optional `annotations` kwarg so both endpoints'
tests can build fixture Applications from one place.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

KEYCLOAK_BASE = "https://keycloak.test"
JWKS_PATH = "/realms/platform/protocol/openid-connect/certs"


@pytest.fixture(autouse=True)
def _point_settings_at_test_backends(monkeypatch):
    monkeypatch.setattr(settings, "keycloak_internal_url", KEYCLOAK_BASE)
    monkeypatch.setattr(settings, "keycloak_public_url", KEYCLOAK_BASE)


@pytest.fixture
def auth_header(sign_token):
    return {"Authorization": f"Bearer {sign_token()}"}


def _mock_jwks(jwk_dict):
    return respx.get(f"{KEYCLOAK_BASE}{JWKS_PATH}").mock(
        return_value=httpx.Response(200, json={"keys": [jwk_dict]})
    )


def _k8s_url() -> str:
    return (
        f"{settings.k8s_api_url}/apis/argoproj.io/v1alpha1/namespaces/"
        f"{settings.argocd_namespace}/applications"
    )


def _mock_k8s(items: list[dict]):
    return respx.get(_k8s_url()).mock(return_value=httpx.Response(200, json={"items": items}))


def _application(name: str, health_status: str, annotations: dict[str, str] | None = None) -> dict:
    metadata: dict = {"name": name}
    if annotations is not None:
        metadata["annotations"] = annotations
    return {"metadata": metadata, "status": {"health": {"status": health_status}}}


@respx.mock
def test_satisfied_when_module_application_is_healthy(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    _mock_k8s([_application("hello-module", "Healthy")])

    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements",
            params={"requires": ["hello-module"]},
            headers={**auth_header, "X-Workspace": "personal"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "results": [{"module_id": "hello-module", "satisfied": True, "status": "Healthy"}]
    }


@respx.mock
def test_not_satisfied_when_module_is_not_installed(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    _mock_k8s([])  # nothing installed

    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements",
            params={"requires": ["hello-module"]},
            headers={**auth_header, "X-Workspace": "personal"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "results": [{"module_id": "hello-module", "satisfied": False, "status": "not installed"}]
    }


@respx.mock
def test_not_satisfied_when_module_is_installed_but_not_yet_healthy(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    _mock_k8s([_application("hello-module", "Progressing")])

    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements",
            params={"requires": ["hello-module"]},
            headers={**auth_header, "X-Workspace": "personal"},
        )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result == {"module_id": "hello-module", "satisfied": False, "status": "Progressing"}


@respx.mock
def test_checks_multiple_requires_independently(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    _mock_k8s([_application("healthy-module", "Healthy"), _application("degraded-module", "Degraded")])

    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements",
            params={"requires": ["healthy-module", "degraded-module", "missing-module"]},
            headers={**auth_header, "X-Workspace": "personal"},
        )

    assert response.status_code == 200
    results = {r["module_id"]: r for r in response.json()["results"]}
    assert results["healthy-module"]["satisfied"] is True
    assert results["degraded-module"]["satisfied"] is False
    assert results["missing-module"] == {
        "module_id": "missing-module",
        "satisfied": False,
        "status": "not installed",
    }


@respx.mock
def test_returns_401_for_missing_authorization(mounted_sa):
    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements",
            params={"requires": ["hello-module"]},
            headers={"X-Workspace": "personal"},
        )
    assert response.status_code == 401


@respx.mock
def test_returns_403_for_no_matching_workspace_membership(jwk_dict, sign_token, mounted_sa):
    _mock_jwks(jwk_dict)
    token = sign_token({"groups": ["/workspaces/other-workspace/viewer"]})
    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements",
            params={"requires": ["hello-module"]},
            headers={"Authorization": f"Bearer {token}", "X-Workspace": "personal"},
        )
    assert response.status_code == 403


@respx.mock
def test_returns_400_for_missing_workspace_hint(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements", params={"requires": ["hello-module"]}, headers=auth_header
        )
    assert response.status_code == 400


@respx.mock
def test_returns_503_when_kubernetes_api_is_unreachable(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    respx.get(_k8s_url()).mock(side_effect=httpx.ConnectError("connection refused"))

    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements",
            params={"requires": ["hello-module"]},
            headers={**auth_header, "X-Workspace": "personal"},
        )

    assert response.status_code == 503


@respx.mock
def test_requires_defaults_to_an_empty_list_and_returns_no_results(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    _mock_k8s([])

    with TestClient(app) as client:
        response = client.get(
            "/modules/check-requirements", headers={**auth_header, "X-Workspace": "personal"}
        )

    assert response.status_code == 200
    assert response.json() == {"results": []}


# GET /modules — feature/gateway-module-registry, 2026-09-08, ui-shell-plan.md item 4.


@respx.mock
def test_lists_an_installed_module_with_its_annotations(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    _mock_k8s(
        [
            _application(
                "hello-module",
                "Healthy",
                annotations={
                    "platform.io/display-name": "Hello Module",
                    "platform.io/icon": "wave",
                    "platform.io/nav-path": "/hello",
                },
            )
        ]
    )

    with TestClient(app) as client:
        response = client.get("/modules", headers={**auth_header, "X-Workspace": "personal"})

    assert response.status_code == 200
    assert response.json() == {
        "modules": [
            {
                "module_id": "hello-module",
                "display_name": "Hello Module",
                "icon": "wave",
                "nav_path": "/hello",
                "status": "Healthy",
            }
        ]
    }


@respx.mock
def test_falls_back_gracefully_for_an_application_with_no_annotations(jwk_dict, auth_header, mounted_sa):
    # Simulates a module installed before this branch existed — its
    # Application was rendered by the old, annotation-less
    # render_application_manifest() and has never been reinstalled.
    _mock_jwks(jwk_dict)
    _mock_k8s([_application("hello-module", "Healthy")])

    with TestClient(app) as client:
        response = client.get("/modules", headers={**auth_header, "X-Workspace": "personal"})

    assert response.status_code == 200
    assert response.json() == {
        "modules": [
            {
                "module_id": "hello-module",
                "display_name": "hello-module",
                "icon": "puzzle",
                "nav_path": None,
                "status": "Healthy",
            }
        ]
    }


@respx.mock
def test_includes_modules_regardless_of_health_status(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    _mock_k8s(
        [
            _application("healthy-module", "Healthy", annotations={"platform.io/display-name": "Healthy"}),
            _application("degraded-module", "Degraded", annotations={"platform.io/display-name": "Degraded"}),
        ]
    )

    with TestClient(app) as client:
        response = client.get("/modules", headers={**auth_header, "X-Workspace": "personal"})

    assert response.status_code == 200
    statuses = {m["module_id"]: m["status"] for m in response.json()["modules"]}
    assert statuses == {"healthy-module": "Healthy", "degraded-module": "Degraded"}


@respx.mock
def test_returns_empty_list_when_nothing_installed(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    _mock_k8s([])

    with TestClient(app) as client:
        response = client.get("/modules", headers={**auth_header, "X-Workspace": "personal"})

    assert response.status_code == 200
    assert response.json() == {"modules": []}


@respx.mock
def test_modules_returns_401_for_missing_authorization(mounted_sa):
    with TestClient(app) as client:
        response = client.get("/modules", headers={"X-Workspace": "personal"})
    assert response.status_code == 401


@respx.mock
def test_modules_returns_403_for_no_matching_workspace_membership(jwk_dict, sign_token, mounted_sa):
    _mock_jwks(jwk_dict)
    token = sign_token({"groups": ["/workspaces/other-workspace/viewer"]})
    with TestClient(app) as client:
        response = client.get(
            "/modules", headers={"Authorization": f"Bearer {token}", "X-Workspace": "personal"}
        )
    assert response.status_code == 403


@respx.mock
def test_modules_returns_400_for_missing_workspace_hint(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    with TestClient(app) as client:
        response = client.get("/modules", headers=auth_header)
    assert response.status_code == 400


@respx.mock
def test_modules_returns_503_when_kubernetes_api_is_unreachable(jwk_dict, auth_header, mounted_sa):
    _mock_jwks(jwk_dict)
    respx.get(_k8s_url()).mock(side_effect=httpx.ConnectError("connection refused"))

    with TestClient(app) as client:
        response = client.get("/modules", headers={**auth_header, "X-Workspace": "personal"})

    assert response.status_code == 503
