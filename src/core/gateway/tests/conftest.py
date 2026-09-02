"""Shared test fixtures — a throwaway RSA keypair, generated once per test
SESSION (not per test — nothing here needs a fresh key per test, and
generating a 2048-bit RSA key is by a wide margin the slowest part of any
test that touches it), used to sign real JWTs. Every auth-related test in
this suite (test_auth.py directly, test_proxy.py through the full ASGI app)
verifies against tokens actually signed this way rather than mocking
`jwt.decode`/`jwt.encode` — that would only prove this code calls a
library function, not that signature/expiry/issuer checking actually works.
"""
from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from app.config import settings

KID = "test-kid-1"


@pytest.fixture(scope="session")
def rsa_keypair() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwk_dict(rsa_keypair: RSAPrivateKey) -> dict:
    """The public half of rsa_keypair, as a JWK dict — what a respx-mocked
    Keycloak JWKS endpoint returns in every test that needs one."""
    algorithm = jwt.algorithms.RSAAlgorithm(jwt.algorithms.RSAAlgorithm.SHA256)
    jwk = json.loads(algorithm.to_jwk(rsa_keypair.public_key()))
    jwk["kid"] = KID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


@pytest.fixture
def sign_token(rsa_keypair: RSAPrivateKey):
    """Returns a function, not a fixed token — every test needs a slightly
    different claim set (expired, wrong issuer, no matching workspace
    group, ...), so this hands back something each test calls with its own
    overrides rather than guessing one default shape that fits everyone.

    `key=` lets a test sign with a DIFFERENT private key than rsa_keypair
    (same `kid`, wrong actual key) — see test_auth.py's
    test_verify_token_rejects_signature_from_a_different_key for why that
    matters: it's what proves signature verification is actually checked,
    not just that a `kid` happens to match something in the JWKS.
    """

    def _sign(claims: dict | None = None, *, kid: str | None = KID, expires_in: int = 300, key=None) -> str:
        payload = {
            "sub": "user-1",
            "iss": settings.expected_issuer,
            "exp": int(time.time()) + expires_in,
            "preferred_username": "alice",
            "groups": ["/workspaces/personal/editor"],
            # A real Keycloak token always carries this (its own default is
            # "account" unless a client's scopes/mappers say otherwise) —
            # included here by default so every test signs a realistic
            # token shape, not one gateway happens to accept only because
            # it's missing a claim real tokens actually have. Found live,
            # 2026-09-02: this fixture's tokens never carried `aud` before,
            # so `verify_token`'s missing `verify_aud: False` (app/auth.py)
            # went untested until a real login hit it — see that file's own
            # comment on the fix.
            "aud": "account",
        }
        if claims:
            payload.update(claims)
        headers = {"kid": kid} if kid is not None else {}
        return jwt.encode(payload, key or rsa_keypair, algorithm="RS256", headers=headers)

    return _sign
