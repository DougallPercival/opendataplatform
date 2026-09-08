# ui-shell

ARCHITECTURE.md §2's "one front door: unified nav... catalog browser... pipeline & run status...
deep-links into each module's own UI... workspace switcher." `docs/architecture/ui-shell-plan.md`
scoped that into 8 separately-decidable pieces; items 1 and 3 are built so far, see below.

## What's built (2026-09-04, feature/ui-shell-scaffold branch)

A real React + TypeScript + Vite scaffold (`npm create vite@latest -- --template react-ts`, not
hand-typed — see `package.json` for the actual generated dependency versions), deployed through the
same GitOps pipeline every other core service uses: a multi-stage `Dockerfile` (Node 22 build stage
→ `nginxinc/nginx-unprivileged` runtime stage — this repo's first multi-stage image; gateway and
catalog-service are both single-stage Python/uvicorn), `manifests/ui-shell.yaml` +
`apps/core/ui-shell.yaml` (mirroring `gateway.yaml`/`apps/core/gateway.yaml`'s structure, sync wave
4 alongside gateway), and a `ci.yml` job pair (`test-ui-shell`/`build-and-push-ui-shell`, mirroring
gateway's). Reachable at `app.platform.local` once deployed — confirmed free when this was scoped,
only `keycloak.platform.local`/`gateway.platform.local` were claimed.

The page itself is deliberately a static placeholder (`src/App.tsx`) — no state, no data fetching,
no auth, nothing calling any backend. The point of this branch is proving the infrastructure (a
frontend build toolchain that didn't exist anywhere in this repo before now, this repo's first
multi-stage Docker image, a fourth Ingress host, a fourth CI job pair) works end-to-end before any
real design decisions get built on top of it — see `ui-shell-plan.md`'s "Recommended first slice."

## What's built (2026-09-08, feature/ui-shell-oauth branch) — Auth

Item 3: real browser login, authorization code + PKCE (RFC 7636), against a new Keycloak client —
`platform-ui-shell`, public, `standardFlowEnabled`, `pkce.code.challenge.method: S256` enforced —
created by `bootstrap/keycloak-bootstrap-ui-shell-client.sh` (see that script's own header for the
full "why a third client" reasoning; it's a separate client from both `platform-cli` and
`platform-cli-login`, same "narrowly-scoped clients cost nothing extra" logic those two already
follow). **One-time step, per cluster, not wired into `bootstrap/install.sh`:** run that script once
(same category as the other two Keycloak-client bootstrap scripts) before login will work — see the
script's own header and its printed output for what it does and confirms.

Hand-rolled, not a library (`oidc-client-ts`/`react-oidc-context`) — `src/auth/` is ~250 lines of
Web Crypto (`crypto.subtle.digest`) and plain `fetch()`, no new runtime dependency beyond `vitest`
for testing it. No router either: `nginx.conf`'s existing SPA fallback already serves
`/auth/callback` to `index.html`, so a single `window.location.pathname` check inside
`AuthProvider`'s mount effect is the entire "routing" this needs. See `src/auth/pkce.ts`,
`config.ts`, `tokens.ts`, `login.ts`, `callback.ts`, `refresh.ts`, `logout.ts`, and
`AuthContext.tsx`/`context.ts`/`useAuth.ts` for the actual implementation — each file's own
docstring covers its piece; `tokens.ts`'s is worth reading first, it states the one rule everything
else follows (ui-shell decodes its own token for display/refresh-timing only, never for an
authorization decision — that stays gateway's job, `app/auth.py`'s `verify_token()`).

Tokens live in `sessionStorage` (not `localStorage`, never a cookie — item 2's already-resolved
bearer-token-in-`Authorization`-header decision carries forward unchanged, so ui-shell calls gateway
with a plain `fetch(url, {headers: {Authorization: 'Bearer ...'}})`, no `credentials: 'include'`).
Silent refresh is scheduled ~60s before each access token's expiry. `App.tsx` still isn't real nav
(that's item 5, a separate future branch, now unblocked by this one) — just a login button when
logged out, the username + a logout button when logged in, enough to prove the flow works end to
end.

**Confirmed no gateway changes needed at all** — `app/auth.py`'s `verify_token()` checks signature/
issuer/`exp`/`sub` with `verify_aud` deliberately off and no client-specific check anywhere, so a
token from this new client is accepted exactly like a `platform-cli-login` token, as long as it
carries the `groups` claim (the bootstrap script adds the same protocol mapper the other two
clients have).

## What's NOT built yet

Items 5 through 8 of `ui-shell-plan.md`'s build list: ui-shell's real nav (unblocked as of this
branch — items 2 and 3 it depended on are both resolved now), gateway's module registry is item 4
(built, `feature/gateway-module-registry`, see `src/core/gateway/README.md`), the Add-ons page's
static release-time module index, Install/Remove buttons (blocked on a real trust-boundary question
— does gateway get git push credentials?), and reverse-proxying into a module's own UI. Each is its
own future branch and its own scoping decision, not a checklist to work through in order — see that
doc for why.

## Running it locally

```bash
npm install
cp .env.example .env   # see that file's own comment for why local dev uses the same real values
npm run dev
```

Vite's dev server listens on `:5173` by default — the same port `catalog-service/.env.example`'s
`CORS_ORIGINS` breadcrumb already referenced before any of this existed, and one of the two origins
`bootstrap/keycloak-bootstrap-ui-shell-client.sh` registers as a valid redirect URI. Real browser
login against `homelab-dev`'s Keycloak needs the same `/etc/hosts` entry the CLI's `platform login`
already requires (see `docs/known-issues.md`'s "Reaching Keycloak by raw IP" entry) — there's no
port-forward equivalent for an interactive browser redirect.

To build and run the actual container image (what CI builds and what Argo CD deploys):

```bash
docker build -t ui-shell:local .
docker run --rm -p 8080:8080 ui-shell:local
# http://localhost:8080
```

## Running its tests

```bash
npm run lint   # oxlint — the Vite react-ts template's default, not ESLint
npm run build  # tsc -b && vite build
npm test       # vitest run
```

Vitest arrived this branch (`feature/ui-shell-oauth`) — the trigger the earlier version of this
README named: real pure logic worth exercising (PKCE math, expiry math, JWT-payload decoding), not
a static placeholder page anymore. Scoped narrowly, matching gateway's own "pure logic direct, live
for the rest" discipline: `src/auth/pkce.test.ts` and `tokens.test.ts` cover the PKCE/token-storage
math directly (including RFC 7636 Appendix B's own worked example, not just self-consistency) —
deliberately *not* testing the actual `fetch()` calls, redirects, or component rendering, since
those need a real Keycloak or a rendered DOM to mean anything. Runs in Vitest's default `node`
environment, not `jsdom` — `crypto.subtle` is a Node 22 global but a known `jsdom` gap, and skipping
it also avoids pulling in `@testing-library/react` for component tests this branch doesn't add.

## What can only be confirmed live

Same category as every other containerized-service branch in this repo: the image doesn't exist
until this branch merges to `dev` (`ci.yml`'s `on.push.branches: [dev, test, main]` only builds and
pushes on those three branches, never on a feature branch) — so whether `app.platform.local` is
actually reachable and the page renders can only be checked on `homelab-dev` after merge.

Specific to this branch: run `bootstrap/keycloak-bootstrap-ui-shell-client.sh` once against the real
cluster (new client, one-time step), then a real browser login end to end — "Log in" redirects to
Keycloak, a real login completes, lands back on `/auth/callback` then `/` with `code`/`state`
stripped from the URL, shows the logged-in username; "Log out" clears the session and returns to
logged-out. Whether `post.logout.redirect.uris` behaves as expected on this cluster's Keycloak
26.7.2 is the one genuinely uncertain detail — see the bootstrap script's own header comment — worth
a `docs/known-issues.md` entry the first time logout is actually checked, either way.
