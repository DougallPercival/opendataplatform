"""Unit tests for verify_token()/derive_headers() — real signed JWTs (see
conftest.py's throwaway RSA keypair) verified against a real JWKSCache
pointed at a respx-mocked JWKS endpoint. This is what proves signature/
expiry/issuer checking actually works, not just that the code calls a
library function correctly.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import AuthError, derive_headers, verify_token
from app.jwks import JWKSCache

BASE_URL = "https://keycloak.test"
JWKS_PATH = "/realms/platform/protocol/openid-connect/certs"


@pytest.fixture
def jwks(jwk_dict):
    with respx.mock:
        respx.get(f"{BASE_URL}{JWKS_PATH}").mock(return_value=httpx.Response(200, json={"keys": [jwk_dict]}))
        client = httpx.AsyncClient(base_url=BASE_URL)
        yield JWKSCache(client=client, jwks_path=JWKS_PATH, ttl_seconds=300)


# ---- verify_token ---------------------------------------------------------


async def test_verify_token_accepts_a_validly_signed_token(sign_token, jwks):
    claims = await verify_token(f"Bearer {sign_token()}", jwks)
    assert claims["sub"] == "user-1"
    assert claims["preferred_username"] == "alice"


async def test_verify_token_rejects_missing_authorization_header(jwks):
    with pytest.raises(AuthError) as exc_info:
        await verify_token(None, jwks)
    assert exc_info.value.status_code == 401


async def test_verify_token_rejects_non_bearer_scheme(jwks):
    with pytest.raises(AuthError) as exc_info:
        await verify_token("Basic dXNlcjpwYXNz", jwks)
    assert exc_info.value.status_code == 401


async def test_verify_token_rejects_empty_bearer_token(jwks):
    with pytest.raises(AuthError) as exc_info:
        await verify_token("Bearer ", jwks)
    assert exc_info.value.status_code == 401


async def test_verify_token_rejects_malformed_token(jwks):
    with pytest.raises(AuthError) as exc_info:
        await verify_token("Bearer not-a-real-jwt", jwks)
    assert exc_info.value.status_code == 401


async def test_verify_token_rejects_expired_token(sign_token, jwks):
    token = sign_token(expires_in=-10)
    with pytest.raises(AuthError, match="expired") as exc_info:
        await verify_token(f"Bearer {token}", jwks)
    assert exc_info.value.status_code == 401


async def test_verify_token_rejects_wrong_issuer(sign_token, jwks):
    token = sign_token({"iss": "https://not-keycloak.example/realms/platform"})
    with pytest.raises(AuthError) as exc_info:
        await verify_token(f"Bearer {token}", jwks)
    assert exc_info.value.status_code == 401


async def test_verify_token_rejects_unknown_kid(sign_token, jwks):
    token = sign_token(kid="some-other-key-id")
    with pytest.raises(AuthError, match="unrecognized") as exc_info:
        await verify_token(f"Bearer {token}", jwks)
    assert exc_info.value.status_code == 401


async def test_verify_token_rejects_missing_kid(sign_token, jwks):
    token = sign_token(kid=None)
    with pytest.raises(AuthError, match="kid") as exc_info:
        await verify_token(f"Bearer {token}", jwks)
    assert exc_info.value.status_code == 401


async def test_verify_token_rejects_signature_from_a_different_key(sign_token, jwks):
    # Same kid as the real keypair, but actually SIGNED with a different
    # key — this is what proves signature verification is genuinely
    # checked, not just that a kid happens to match something in the JWKS.
    impostor_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = sign_token(key=impostor_key)
    with pytest.raises(AuthError) as exc_info:
        await verify_token(f"Bearer {token}", jwks)
    assert exc_info.value.status_code == 401


# ---- derive_headers ---------------------------------------------------


def test_derive_headers_picks_the_matching_workspace_role():
    claims = {"preferred_username": "alice", "groups": ["/workspaces/personal/editor"]}
    derived = derive_headers(claims, "personal")
    assert derived.workspace == "personal"
    assert derived.user == "alice"
    assert derived.role == "editor"


def test_derive_headers_falls_back_to_sub_when_no_preferred_username():
    claims = {"sub": "user-1", "groups": ["/workspaces/personal/viewer"]}
    derived = derive_headers(claims, "personal")
    assert derived.user == "user-1"


def test_derive_headers_ignores_group_memberships_in_other_workspaces():
    claims = {"preferred_username": "alice", "groups": ["/workspaces/other-workspace/owner"]}
    with pytest.raises(AuthError) as exc_info:
        derive_headers(claims, "personal")
    assert exc_info.value.status_code == 403


def test_derive_headers_requires_a_workspace_hint():
    claims = {"preferred_username": "alice", "groups": ["/workspaces/personal/editor"]}
    with pytest.raises(AuthError) as exc_info:
        derive_headers(claims, None)
    assert exc_info.value.status_code == 400


def test_derive_headers_requires_some_group_membership():
    claims = {"preferred_username": "alice", "groups": []}
    with pytest.raises(AuthError) as exc_info:
        derive_headers(claims, "personal")
    assert exc_info.value.status_code == 403


def test_derive_headers_picks_highest_privilege_when_multiple_roles_match():
    # Not something `platform workspace invite` itself would ever produce,
    # but nothing stops a human admin from adding someone to more than one
    # role-group under the same workspace by hand — see _ROLE_PRIORITY's
    # own comment in auth.py for why "owner" has to win here, not whichever
    # one the token's groups claim happens to list first.
    claims = {
        "preferred_username": "alice",
        "groups": ["/workspaces/personal/viewer", "/workspaces/personal/owner"],
    }
    derived = derive_headers(claims, "personal")
    assert derived.role == "owner"


def test_derive_headers_as_headers_shape():
    claims = {"preferred_username": "alice", "groups": ["/workspaces/personal/editor"]}
    derived = derive_headers(claims, "personal")
    assert derived.as_headers() == {"X-Workspace": "personal", "X-User": "alice", "X-Role": "editor"}
