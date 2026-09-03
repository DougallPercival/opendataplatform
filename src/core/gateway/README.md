# gateway

ARCHITECTURE.md §2 (layer 3), §3, §10's `platform-gateway` — but a much narrower slice of it than
that document describes, built for one specific reason: `catalog-service/app/deps.py` always trusted
client-declared `X-Workspace`/`X-User`/`X-Role` headers with zero verification, and gateway was the
named fix (see that file's own docstring). This is that fix, and nothing more yet.

## What's built (2026-09-02, platform-gateway-auth branch)

Real Keycloak JWT verification (signature via JWKS, expiry, issuer — see `app/auth.py`) plus a
transparent, streaming proxy to `catalog-service` (`app/proxy.py`) that forwards
`X-Workspace`/`X-User`/`X-Role` — but now gateway-derived from the verified token's claims, never
client-declared. `X-Workspace` stays a client-supplied *hint* (preserves `platform-cli`'s existing
`--workspace` flag), validated against the token's `groups` claim before it's trusted; no match → 403.
`X-User`/`X-Role` are never anything a caller sends — see `app/auth.py`'s `derive_headers()` docstring
for the full design, and `platform_sdk/client.py`'s module docstring for the client side of the same
story.

`platform login` (`platform_sdk/keycloak_login.py`, `platform-cli/platform_cli/login.py`) is the
device-flow command that gets a real user a token to send here — see those modules' own docstrings.

## What's NOT built yet — the rest of ARCHITECTURE.md's gateway scope

Nothing here does nav aggregation, the Add-ons page API, the module registry, or reverse-proxying into
other modules' own UIs (ARCHITECTURE.md §3). There's nothing to serve nav to yet — `ui-shell` doesn't
exist — so building those now would be speculative. This service currently proxies to exactly one
backend, `catalog-service`, at one fixed URL; the module-registry-driven "figure out where to proxy
based on `modules/*/module.yaml` + live `PlatformModule` registrations + Argo CD `Application` status"
piece ARCHITECTURE.md §3 describes is future work once there's a second module to proxy to.

NetworkPolicy enforcement isolating catalog-service's namespace ingress to gateway's namespace only is
also deferred — see `docs/known-issues.md`. k3s's bundled Network Policy controller is enabled by
default and WOULD enforce a policy restricting this, but no such policy exists yet: today, any other
in-cluster pod can still reach catalog-service's ClusterIP directly and forge these same three headers.
Gateway closes the *application-layer* gap (nothing reaching catalog-service through gateway can forge
identity/role anymore); the *network-layer* gap — bypassing gateway entirely — is still open.

## Running it locally

Needs a reachable Keycloak (for JWKS) and catalog-service. Against a real cluster, port-forward both
(see `.env.example` for the exact env vars to point at each) the same way `catalog-service`'s own
README documents for direct testing:

```bash
cp .env.example .env
# edit .env — see its own comments for what each GATEWAY_* var does and why
# there are two different Keycloak URLs
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Then, with `platform login` run against the same cluster and `PLATFORM_GATEWAY_URL` pointed at this
service:

```bash
curl -H "Authorization: Bearer $(cat ~/.config/platform/credentials.json | jq -r .access_token)" \
  -H "X-Workspace: personal" http://localhost:8080/me
```

## Running its tests

```bash
pip install -e ".[dev]"
ruff check .
pytest -v
```

No live Keycloak or catalog-service needed — same discipline as `platform-sdk`'s own
`test_keycloak_admin.py`/`test_keycloak_login.py`:

- `tests/conftest.py` generates a throwaway RSA keypair once per test session and signs real JWTs with
  it — `tests/test_auth.py` proves `verify_token()`/`derive_headers()` actually check a signature,
  expiry, and issuer correctly, not just that the code calls a library function. `tests/test_jwks.py`
  respx-mocks Keycloak's JWKS endpoint to prove the cache-hit vs. refresh-on-unknown-kid behavior in
  `app/jwks.py`.
- `tests/test_proxy.py` uses FastAPI's `TestClient` (which drives the real `lifespan`, so the real
  `httpx.AsyncClient`s get constructed) with `respx` intercepting both the Keycloak and catalog-service
  base URLs — proving header stripping/injection, streaming, and the 502/504-on-backend-unreachable
  behavior end to end through the actual ASGI app, not a hand-rolled fake of it.

## What can only be confirmed live

Same category as everything else Keycloak-touching in this repo (see `platform-sdk`'s and
`platform-cli`'s own READMEs for the equivalent sections there): whether `keycloak-tls`'s Certificate
SAN fix actually resolves TLS from inside a real gateway pod connecting to Keycloak's in-cluster Service
DNS name; whether `platform-ca-secret`'s Reflector mirror into this namespace actually lands the CA file
at the path this service expects; the real device-flow UX end to end, through a real browser, through
this service, to a real catalog-service response.
