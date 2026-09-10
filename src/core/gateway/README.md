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

`GET /modules/check-requirements` (`app/modules.py`, `app/argocd.py`, platform-module-deps branch,
2026-09-03) — module-lifecycle-plan.md item 6: "is module X installed and healthy," checked live
against Argo CD `Application` state via the Kubernetes API (RBAC: a dedicated `gateway`
ServiceAccount scoped read-only to `applications.argoproj.io` in the `argocd` namespace — see
`argocd/README.md`'s new RBAC section). `platform module install` is the first caller — see
`platform_cli/module.py`'s `_check_requires`. Same auth as the proxy (verified token + workspace
membership); ARCHITECTURE.md §3's "the dependency check lives once, at the API layer both doors
call through" — this is that one place, and a future Add-ons page (item 7, still not built) would
call the exact same endpoint.

## CORS support for ui-shell (2026-09-08, feature/gateway-cors-ui-shell branch)

`docs/architecture/ui-shell-plan.md` item 2: does gateway grow a second proxy target to serve
`ui-shell` same-origin, or does `ui-shell` keep its own Ingress host with gateway adding CORS
instead? `app/proxy.py` turned out to be hardcoded to exactly one backend (`app.state.catalog_client`,
no dispatch layer, auth applied uniformly to every request) — folding `ui-shell` in would have meant
real restructuring, plus an explicit keep-or-remove call on its already-live Ingress/Certificate.
Went with CORS: `app/config.py`'s `cors_origins`/`cors_origin_list` (same shape as
`catalog-service/app/config.py`'s own, already-proven copy) and `app/main.py`'s `configure_cors()`
(a plain function, not just an inline conditional, specifically so `tests/test_cors.py` can exercise
the "configured" case directly — see that function's own docstring). `GATEWAY_CORS_ORIGINS` is set
for real in `argocd/manifests/gateway.yaml`'s Deployment (`https://app.platform.local`, matching
`manifests/ui-shell.yaml`'s Ingress exactly) — unlike catalog-service's same-named setting, which is
unused in-cluster (a NetworkPolicy means a browser never reaches it directly), this one carries real
production traffic once `ui-shell` actually starts calling gateway (items 4/5).

`allow_credentials` stays at its default (`False`): identity stays bearer-token-in-`Authorization`-
header, the same pattern `platform_sdk`/`platform-cli` already use, never a cookie — so item 3
(browser OAuth) inherits "no cross-origin-cookie complications" as a side effect of this decision,
not something it has to solve itself.

## Module registry v1 — installed modules only (2026-09-08, feature/gateway-module-registry branch)

`docs/architecture/ui-shell-plan.md` item 4: `GET /modules` lists every installed module with the
fields ui-shell's future nav (item 5) needs to render it — `display_name`, `icon`, `nav_path`, plus
live `status`. Same shape as `check-requirements` above: `require_auth` first (401/400/403 exactly
as that endpoint), then a Kubernetes API read, 503 on `ArgoCDUnavailableError`. Deliberately
installed-only, every status included (not filtered to `Healthy`) — ui-shell decides how to render a
degraded module, this endpoint just reports what's really there, the same discipline
`list_module_applications()` already applied to health status.

The real work was propagation, not a new endpoint: `platform_cli/manifest.py`'s
`render_application_manifest()` already had `displayName`/`icon`/`navPath` on `ModuleManifest`
(module.yaml's own schema) but wrote none of them into the deployed `Application` anywhere. Now it
writes them as `platform.io/display-name`/`platform.io/icon`/`platform.io/nav-path` annotations —
through `json.dumps()`, not raw f-string interpolation, since `displayName` is free-form operator
text that could otherwise contain a colon or quote and corrupt the generated YAML.
`app/argocd.py`'s new `list_module_summaries()` (a sibling to `list_module_applications()`, sharing
the same underlying Kubernetes API call via a factored-out `_fetch_module_application_items()`)
reads those annotations back out. An `Application` rendered before this branch — any module
installed and never reinstalled — simply has none of these annotations; `list_module_summaries()`
falls back to `module_id`/`"puzzle"`/`None` rather than erroring, so `GET /modules` still works
against everything already live, not just a freshly-`platform module install`ed module.

Auth: `GET /modules` reuses `require_auth` exactly like `check-requirements`, not a new
workspace-optional variant — following the precedent that endpoint's own docstring already set for
this exact situation (module `Application`s aren't workspace-scoped resources, but scoping this by
workspace membership anyway doesn't leak anything an authenticated user couldn't already infer).

`proxyTo` is still not propagated anywhere — that stays item 8's own future pass over
`render_application_manifest()`, deliberately untouched here.

## Add-ons page's static module catalog (2026-09-09, feature/gateway-module-catalog branch)

`docs/architecture/ui-shell-plan.md` item 6 — the static half `GET /modules` above deliberately
left out: `GET /modules/catalog` lists **every** module under `src/modules/`, installed or not
(ARCHITECTURE.md §3: "so it can list modules that aren't installed yet"), overlaid with live status
from the same `list_module_applications()` `check-requirements` already uses. Backend only — the
actual Add-ons *page* rendering this in `ui-shell` is a future item, the same split item 4 (this
endpoint's installed-only sibling) had from item 5 (the ui-shell page that eventually called it).

The static half of the catalog — one JSON file, `{modules: [{id, displayName, icon, navPath,
requires, optional}, ...]}` — is generated by `platform-cli`'s new `platform module build-index
<out-path>` (`platform_cli/module_index.py`'s `build_static_module_index()`, pure and unit-tested
on its own), not by gateway itself: gateway's own Docker build context stays scoped to
`src/core/gateway` (it can't see `src/modules/` or `platform-cli`'s parsing code), so a new step in
`build-and-push-gateway` (`ci.yml`) runs that command *before* the Docker build, writing straight
into `src/core/gateway/app/module_catalog.json` — inside the build context the Dockerfile's
existing `COPY app ./app` already picks up, no Dockerfile change needed. `src/modules/**` joined
gateway's own CI path filter as part of this: without it, a module-only change (adding or editing a
`module.yaml`) triggered nothing at all.

`app/module_index.py`'s `load_static_module_index()` is the runtime read side — a missing or
malformed file degrades to an empty catalog (logged as a warning), never a crash or a 503; a
genuinely different failure mode than `ArgoCDUnavailableError`, since nothing else behind gateway
depends on this file. This also means a local `docker build` (or `uvicorn` run) without first
running the generator by hand still boots a working gateway, just with an empty Add-ons catalog —
see "Running it locally" below for the one-line command to populate it.

Display fields (`display_name`/`icon`/`nav_path`) always come from the static file, never from a
live Application's `platform.io/*` annotations, even for an installed module: the static file is
regenerated on every gateway release, so it can't go stale the way a long-installed, never-
reinstalled module's Application annotations can — ARCHITECTURE.md §3 scopes the live overlay to
*state* only. `requires`/`optional` ride along even though nothing here computes satisfaction from
them — `check-requirements` above still owns that; a future Install button (item 7) is the actual
consumer.

## Install/uninstall dispatch (2026-09-10, feature/gateway-module-lifecycle-dispatch branch)

`docs/architecture/ui-shell-plan.md` item 7's mutation mechanism — the backend half only (wiring the
Add-ons page's own Install button is a separate future branch, same item-6→7 split the static catalog
above already used). `POST /modules/{module_id}/install` and `POST /modules/{module_id}/uninstall`
don't touch git themselves: gateway holding a git-write credential was the one design this branch
explicitly rejected. Instead each endpoint calls `app/github_dispatch.py`'s
`trigger_module_workflow()`, which asks the GitHub API to start a `workflow_dispatch` run of
`.github/workflows/module-lifecycle.yml` — that workflow (unchanged `platform module
install`/`uninstall` underneath) does the actual commit and push, authenticated with GitHub Actions'
own ephemeral, auto-scoped `GITHUB_TOKEN` (the same credential `ci.yml` already uses for GHCR pushes,
here granted `contents: write` instead of `packages: write`, and only for that one job). Both
endpoints are fire-and-forget: a `202` means the workflow was told to start, nothing here waits for or
reports back on how it went — `GET /modules/catalog`/`GET /modules` (above) are how a future UI would
eventually observe the status flip once Argo CD reconciles the workflow's commit.

`require_role()` (`app/auth.py`) is new and is the first thing in this service to check
`derived.role` for anything beyond plain membership — every route before this branch only ever
confirmed `derive_headers()` didn't raise at all. Both endpoints require at least **editor**:
`_ROLE_PRIORITY`'s existing `owner > editor > viewer` ordering, viewer gets `403`. `install` also
reuses `check-requirements`'s own comparison (`list_module_applications()` +
`_SATISFIED_STATUS`/`_NOT_INSTALLED_STATUS`) for a `409` on unsatisfied `requires` — "the dependency
check lives once, at the API layer every door calls through" now has a third caller, not a second
implementation — and does **not** block reinstalling an already-`Healthy` module, since
`platform_cli/manifest.py`'s own docstring already documents that as safe.

### GitHub PAT for the module-lifecycle dispatch

`GATEWAY_GITHUB_TOKEN` (`app/config.py`'s `github_token`) is a **fine-grained GitHub Personal Access
Token, scoped to this one repo, with Repository permission `Actions: Read and write` and nothing
else** — specifically **not** `Contents`, since the actual git commit/push never uses this token (see
above). Create it under GitHub → Settings → Developer settings → Fine-grained tokens → "Only select
repositories" → this repo, then run `bootstrap/seal-gateway-github-token.sh` from a real checkout with
`kubectl`/`kubeseal` on `PATH` — it prompts for the PAT (hidden input, never a CLI argument or env
var), fetches your own `homelab-dev` cluster's real sealed-secrets public cert, and overwrites the
placeholder `SealedSecret` document at the bottom of `argocd/manifests/gateway.yaml` with the real
sealed ciphertext. You still commit and push that file yourself — the script never touches git. This
is this repo's **first** real use of sealed-secrets: the controller (`apps/core/sealed-secrets.yaml`)
has been deployed since day one but never actually had anything sealed with it before this branch.
Once the sealed value is pushed and the controller has decrypted it into a real `Secret`
(`kubectl -n gateway get secret gateway-github-token`), roll gateway out
(`kubectl -n gateway rollout restart deployment/gateway`) to pick it up — a missing/unconfigured token
degrades to a `503` on both endpoints (`GitHubDispatchError`), never a crash.

## Force cleanup — closing the modules-root prune gap (2026-09-10, feature/force-cleanup branch)

`docs/known-issues.md` documents a recurring, not-fully-root-caused Argo CD quirk: after an
uninstall's own workflow succeeds and `modules-enabled/<id>.yaml` is gone from git,
`modules-root`'s automated `prune: true` sync sometimes still shows `PruneSkipped` for the now-
orphaned child `Application` instead of actually deleting it. The only workaround that's ever
reliably closed the gap is a manual `sudo kubectl -n argocd delete application <module-id>` — this
branch makes that click-able instead of requiring a terminal and cluster access.

`POST /modules/{module_id}/force-cleanup` (`app/modules.py`) is synchronous — unlike
install/uninstall's GitHub Actions dispatch, there's no workflow to trigger: `app/argocd.py`'s new
`delete_module_application()` issues the Kubernetes API `DELETE` directly and the endpoint returns
`200` once that call completes (`202` stays install/uninstall's signal for "a workflow was told to
start," which doesn't apply here). Same `require_role(derived, "editor")` bar and the same
"installed?" check `uninstall` already makes (404 if there's no live Application to clean up).

**RBAC**: reuses the exact same gateway ServiceAccount token every read above already holds — the
`Role` in `argocd/manifests/gateway.yaml` was broadened to add the `delete` verb (renamed
`gateway-read-module-applications` → `gateway-module-applications` since it's no longer read-only).
Deliberately **not** a second, Argo CD-native REST API credential: a plain Kubernetes `DELETE`
against the `Application` object triggers the exact same `resources-finalizer.argocd.argoproj.io`
cascade a `kubectl delete` or Argo CD's own REST API delete would — one fewer credential shape for
an identical outcome. See `app/argocd.py`'s `delete_module_application()` docstring and
`argocd/README.md`'s RBAC section for the full reasoning.

**Never fires automatically** — only a user clicking a "Force cleanup" button ui-shell shows once an
uninstall's own polling window times out (see `src/core/ui-shell/README.md`). Firing it the instant a
timeout is detected would risk a real race: the uninstall workflow's own git push can still land
*after* gateway's poll gives up waiting, and deleting the Application while that push is in flight
risks `modules-root` recreating it right back from the (about to be deleted) `modules-enabled/*.yaml`
that's still mid-flight to git. Waiting for a deliberate click is what avoids that.

## What's NOT built yet — the rest of ARCHITECTURE.md's gateway scope

Dependency-checking, CORS, the installed-modules registry, the Add-ons page's static catalog, and the
install/uninstall dispatch mechanism are real now (above), and `ui-shell` itself now calls
`GET /modules` for its real nav (`docs/architecture/ui-shell-plan.md` item 5, `feature/ui-shell-nav`
branch, 2026-09-09 — see `src/core/ui-shell/README.md`). Still not built: the Add-ons *page* itself in
`ui-shell` actually calling the two new endpoints above (its Install button is still disabled), and
reverse-proxying into other modules' own UIs (ARCHITECTURE.md §3, item 8). This service still proxies
to exactly one backend, `catalog-service`, at one fixed URL; reverse-proxying into a module's own UI
(item 8) is future work once `proxyTo` is propagated the same way `displayName`/`icon`/`navPath` are
here.

NetworkPolicy enforcement isolating catalog-service's namespace ingress to gateway's namespace only —
**built and confirmed live, `catalog-service-netpol` branch, 2026-09-03** (this paragraph corrected
2026-09-11; it had gone stale, still describing this as deferred well after it shipped — see
`docs/known-issues.md`'s "catalog-service's auth was a placeholder" entry for the full writeup and
live-verification). `src/core/argocd/manifests/catalog-service.yaml` carries a `NetworkPolicy`
restricting ingress into catalog-service's pods to only the `gateway` namespace, on the one port it
exposes. Gateway closes the *application-layer* gap (nothing reaching catalog-service through gateway
can forge identity/role); the NetworkPolicy closes the *network-layer* one (nothing outside the
`gateway` namespace can reach catalog-service's ClusterIP at all anymore, verified with a disposable
pod elsewhere in the cluster getting a real `Connection refused`).

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

`GET /modules/catalog` needs one more thing locally that a real Deployment always has: the static
module catalog `build-and-push-gateway` (ci.yml) generates before every image build. Without it,
`load_static_module_index()` (`app/module_index.py`) just logs a warning and the endpoint returns
an empty list — a working, honest response, but not a useful one for testing that endpoint. To
populate it by hand (from the repo root, with `platform-sdk`/`platform-cli` installed per their own
READMEs):

```bash
platform module build-index src/core/gateway/app/module_catalog.json
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
