"""verify_token() and derive_headers() — the pure, fully-unit-testable
"brain" of gateway's auth. proxy.py's catch-all route calls both, in order,
and turns whatever AuthError either one raises into the matching HTTP
response; neither function here talks HTTP itself (jwks.py owns the one
network call verify_token() needs) specifically so this module can be
tested with plain unit tests against real signed JWTs (see
tests/conftest.py's throwaway RSA keypair and tests/test_auth.py) rather
than mocking `jwt.decode` itself — that would only prove this module calls
a library function, not that the actual verification logic is correct.
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt

from app.config import settings
from app.jwks import JWKSCache

# Highest-privilege first. Nothing in Keycloak stops a human admin from
# adding someone to more than one role-group under the same workspace by
# hand (e.g. both .../editor and .../owner) even though `platform workspace
# invite` itself only ever adds one — derive_headers() below picks the
# FIRST entry here that has a matching group, i.e. the most-privileged one,
# rather than whatever order the token's own `groups` claim happens to list
# them in (unspecified, and not something to rely on).
_ROLE_PRIORITY = ("owner", "editor", "viewer")


class AuthError(Exception):
    """Raised by verify_token()/derive_headers() for anything that should
    become an HTTP error response. Carries the intended status code so
    proxy.py doesn't need a chain of isinstance checks to know whether a
    given failure means 401 (no/bad/expired token — try authenticating
    again) or 403 (a genuinely valid token, just not a member of the
    workspace that was asked for) or 400 (the request itself was malformed,
    e.g. no X-Workspace at all)."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")


@dataclass
class DerivedHeaders:
    """What derive_headers() decided, in a shape proxy.py can turn straight
    into outbound request headers via as_headers() — kept as a small
    dataclass rather than a bare dict so callers/tests can access
    `.workspace`/`.user`/`.role` by name instead of magic string keys."""

    workspace: str
    user: str
    role: str

    def as_headers(self) -> dict[str, str]:
        return {"X-Workspace": self.workspace, "X-User": self.user, "X-Role": self.role}


async def verify_token(authorization: str | None, jwks: JWKSCache) -> dict:
    """`authorization` is the raw `Authorization` header value (or None) —
    checking for the `Bearer ` prefix is this function's job, not the
    caller's, so proxy.py has exactly one place that defines what "not
    authenticated" means. Returns the verified claims dict on success;
    raises AuthError(401, ...) for every failure mode (missing header,
    malformed token, unknown signing key, bad signature, expired, wrong
    issuer) — proxy.py doesn't need to distinguish any of those from each
    other, only from AuthError(403, ...) (see derive_headers()).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError(401, "Missing or malformed Authorization header — expected 'Bearer <token>'.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthError(401, "Missing or malformed Authorization header — expected 'Bearer <token>'.")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise AuthError(401, f"Malformed token: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise AuthError(401, "Token header has no 'kid' — can't look up which key signed it.")

    jwk = await jwks.get_key(kid)
    if jwk is None:
        raise AuthError(
            401, f"Token was signed with an unrecognized key (kid={kid!r}) — not one of Keycloak's "
            "current signing keys."
        )

    try:
        public_key = jwt.PyJWK.from_dict(jwk).key
        claims = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256"],
            issuer=settings.expected_issuer,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(401, "Token has expired — run `platform login` again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(401, f"Token failed verification: {exc}") from exc

    return claims


def derive_headers(claims: dict, x_workspace: str | None) -> DerivedHeaders:
    """`claims` must already be verify_token()'s return value — never call
    this with claims that haven't been signature/exp/iss-checked.
    `x_workspace` is the client-supplied hint (the inbound request's own
    X-Workspace header, if any): still just a hint at this point, nothing
    about it being client-supplied makes it trusted — that's exactly what
    this function checks, against `claims["groups"]`, before it's allowed
    to become the outbound X-Workspace.

    Raises AuthError(400, ...) if no X-Workspace hint was sent at all (there
    has to be SOME workspace to check membership against), and
    AuthError(403, ...) if the token's `groups` claim has no entry under
    `/workspaces/<x_workspace>/...` — a verified identity with no
    membership in the workspace it's asking for.
    """
    if not x_workspace:
        raise AuthError(400, "X-Workspace header is required.")

    user = claims.get("preferred_username") or claims.get("sub")
    if not user:
        raise AuthError(401, "Token has neither 'preferred_username' nor 'sub' — can't identify the caller.")

    groups: list[str] = claims.get("groups") or []
    prefix = f"/workspaces/{x_workspace}/"
    member_roles = {
        group[len(prefix):]
        for group in groups
        if group.startswith(prefix) and group[len(prefix):] in _ROLE_PRIORITY
    }
    for role in _ROLE_PRIORITY:
        if role in member_roles:
            return DerivedHeaders(workspace=x_workspace, user=user, role=role)

    raise AuthError(
        403,
        f"Not a member of workspace {x_workspace!r} (no matching group in this token's 'groups' claim) "
        "— ask a workspace owner to `platform workspace invite` you.",
    )
