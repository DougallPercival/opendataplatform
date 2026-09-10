# ui-shell

ARCHITECTURE.md §2's "one front door: unified nav... catalog browser... pipeline & run status...
deep-links into each module's own UI... workspace switcher." `docs/architecture/ui-shell-plan.md`
scoped that into 8 separately-decidable pieces; items 1, 3, and 5 are built so far, see below.

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

## What's built (2026-09-09, feature/ui-shell-nav branch) — Real nav

Item 5: ui-shell stops being a login button and becomes an actual app. `react-router@^8.3.1`
(declarative mode — `BrowserRouter`/`Routes`/`Route`, not the data router; there are no
loaders/actions anywhere here, and the route tree needs to be conditionally absent while
unauthenticated, which fits an ordinary `if` far better) is the first routing library in this repo.
`App.tsx` now splits into `/auth/callback` (`auth/AuthCallbackRoute.tsx`) and everything else
(`Gate`, which shows `shell/LoginScreen.tsx` or `shell/Shell.tsx` depending on `useAuth().status`).
`Shell` owns its own nested `<Routes>` — `/` (the module list) and `modules/:moduleId` (a detail
page) — so nothing under it (the workspace switcher, the module list, any gateway call) ever mounts
for an unauthenticated visitor.

**A real bug fixed along the way**: `auth/callback.ts` used to call
`window.history.replaceState({}, '', '/')` directly to strip `?code&state` from the URL after a
login. That call fires no `popstate` event, so a mounted client router would never see it — the URL
bar would read `/` while the router's own internal location stayed stuck on `/auth/callback`. Fixed
by removing that call and adding the router-side cleanup to `auth/AuthCallbackRoute.tsx` instead,
via `useNavigate()`. `AuthContext.tsx`'s own `isCallbackPath()`/`callbackHandled` guard needed no
changes — it only ever reads `window.location.pathname` once, never touches history.

**Workspace switcher** (`src/workspace/`): there is no "list my workspaces" endpoint anywhere in the
platform, and gateway requires an `X-Workspace` header on every call — the only source is the ID
token's own `groups` claim (`/workspaces/<name>/<role>` entries), decoded client-side via the
*existing* `decodeJwtPayload` from `auth/tokens.ts` (same display-only precedent it already
established for `preferred_username` — this never makes an authorization decision, that stays
gateway's job). `workspaces.ts`'s `parseWorkspaceMemberships()` mirrors gateway's own
`derive_headers()` exactly (`owner`/`editor`/`viewer` only, same dedupe priority). Selection persists
in `sessionStorage`.

**Module list and detail** (`src/modules/`): `GET /modules` (item 4) via plain `fetch()` in a
hand-rolled hook (`useModules.ts`) — no data-fetching library, one GET endpoint refetched on
workspace change, same "hand-rolled for a narrow mechanism" choice this repo has made twice already
(gateway's raw `httpx`, ui-shell's own PKCE). Clicking a module routes to `/modules/<module_id>`
(keyed by `module_id`, not raw `nav_path` — `nav_path` is completely unvalidated server-side and can
be `null`) and shows real live data from the already-fetched list, with an honest note that opening
the module's own UI is item 8, not built yet — not a fake iframe or dead link. Icons are a small
hand-drawn local SVG map (`modules/icons.tsx`) with a fallback glyph, not an icon-library dependency
— `icon` is a completely free-form string with no enforced value set anywhere in the platform.

Styling moved to CSS Modules (`Component.module.css` next to each `Component.tsx`) — `index.css` now
holds only true globals. No UI framework introduced.

## What's built (2026-09-09, feature/ui-shell-addons branch) — Add-ons page

Item 7 of `ui-shell-plan.md`, scoped to a **read-only page only** — confirmed with the repo owner
this session. Item 7 as titled ("Install/Remove buttons") presupposed a page that didn't exist yet,
and its real mechanism hinges on an unresolved trust-boundary question (does gateway get git-write
credentials?). That question is now decided but not built: when the mutation mechanism lands (a
future branch), gateway will trigger a GitHub Actions `workflow_dispatch` rather than holding a
direct git/PAT credential, reusing the same ephemeral, auto-scoped `GITHUB_TOKEN` pattern `ci.yml`
already uses for GHCR pushes. Nothing in this branch calls it — no mutation fires from this page at
all.

New `src/addons/` directory (its own top-level concern, alongside `auth/`, `modules/`, `shell/`,
`workspace/`), consuming item 6's `GET /modules/catalog` via a hand-rolled `fetch()` hook
(`useAddons.ts`) that's a structural copy of `modules/useModules.ts` — same derived-at-render
idle/loading/success/error state machine, same per-fetch `AbortController`. `Addons.tsx` lists every
module in the catalog (installed or not), reusing `modules/icons.tsx`'s `ModuleIcon` and a copy of
`ModuleList.module.css`'s status-badge palette. Each row shows its `requires` list as plain text (no
dependency-satisfaction computation here — `check-requirements` still owns that) and a disabled
`Install` button with a `title` tooltip; a page-level notice states plainly that installing/removing
isn't built yet. A module already installed links its name to the existing
`/modules/:moduleId` detail page instead of showing the disabled button — `ModuleDetail.tsx` needed no
changes, it already looks up by `moduleId` against its own list independent of how the user navigated
there.

`shell/Shell.tsx` gained a `/addons` route and a small nav (`NavLink` — the first use of it in this
repo, purely for the free active-link styling over plain `Link`) to switch between "Modules" and
"Add-ons".

## What's built (2026-09-10, feature/ui-shell-addons-mutation branch) — Install/Remove

The rest of item 7: the Add-ons page's Install/Remove buttons now actually call gateway's mutation
endpoints (`POST /modules/{id}/install` and `.../uninstall`, `feature/gateway-module-lifecycle-dispatch`
— already live-verified end to end against `homelab-dev`, including both the kubeseal-KUBECONFIG and
GitHub-default-branch wrinkles it hit). Both endpoints are fire-and-forget: a `202` just means "queued,"
so most of this branch is what happens after that — turning "queued" into something a person looking at
the page can actually make sense of, without pretending to know when it's really done.

New `src/addons/mutations.ts` (pure `fetch()` wrapper, same shape as `modules/api.ts`'s calls) posts to
the two endpoints and turns a non-2xx response into a typed `AddonMutationError` (carrying `status`,
`detail`, and — for a `409` — `unsatisfied`, gateway's list of unmet `requires`). State lives in a new
pure/React split, the same pattern `workspace/workspaces.ts`/`WorkspaceContext.tsx` already established:
`src/addons/mutationState.ts` is the pure state machine (`submitting` → `queued` → resolved, or
`timed-out` if polling runs out, or `error`), and `src/addons/useAddonMutations.ts` is the React hook
wrapping it — one hook instance for the whole page (a single `Map` keyed by `moduleId`), not one per
row, so every row renders off the same source of truth. Per this session's scoping decision, a `queued`
mutation triggers a short polling window against the already-fetched catalog (`useAddons`'s own
`refetch`, every 5s for up to 2 minutes): the moment the catalog's status for that module flips to what
the mutation was waiting for, the entry resolves itself and the row goes back to normal — no separate
"did it work?" endpoint, this just rides `GET /modules/catalog`, the same one the page already polls
implicitly by refetching. If the window runs out first, the row falls back to a plain "still processing"
note instead of a spinner that never resolves.

Getting the queued→resolved resolution right meant deliberately *not* reacting to `entries` inside a
`useEffect` — an early draft did exactly that and oxlint's `react(set-state-in-effect)` rule caught it
immediately, the same "computed at render time, never set synchronously inside an effect body"
discipline `useAddons.ts`'s own docstring already documents. The fix: `rawStates` only changes on real
events (a click, or the periodic timeout sweep), and the publicly-used `states` value is derived at
render time by diffing `rawStates` against the latest `entries` — no effect involved in that step at
all, only in the polling interval itself, which starts and stops on `hasAnyQueued(states)`.

Role-gating: `Addons.tsx` reads `role` off `useWorkspace()`'s existing `WorkspaceMembership` data (no
new API call) and disables both buttons with a tooltip for a `viewer`, mirroring gateway's own
`require_role(derived, "editor")` — a UX nicety only, gateway re-checks on every request regardless of
what the client shows. Remove goes through an inline confirm/cancel step (not a native `confirm()`
dialog — matches this repo's stated avoidance of those elsewhere) with copy noting removal can take a
few minutes to fully complete; that caveat is deliberate, not filler — `docs/known-issues.md` documents
a real, still-open `modules-root` prune gap that can leave an uninstalled module's `Application` object
lingering until someone runs the documented manual `kubectl delete application` workaround, and this
branch ships Remove with that caveat rather than blocking on fixing the gap first (this session's own
scoping decision).

## What's built (2026-09-10, feature/module-proxy branch) — a module's own UI, embedded

Item 8 — the last item on `ui-shell-plan.md`'s build list. `ModuleDetail.tsx` used to show a static
"isn't built yet" notice for every module; a module with `hasOwnUi` (new field on `Module`/`ModuleDto`,
`src/modules/api.ts`, reflecting gateway's own new `has_own_ui`) now renders an embedded `<iframe>`
showing that module's actual UI, right below the existing metadata block.

Gateway is deliberately bearer-`Authorization`-header-only (see `src/core/gateway/README.md`'s CORS
section), and a plain `<iframe src>` navigation structurally can't send a custom header — so this
needed a real mechanism, not just a new `<iframe>` tag. New `src/modules/proxyToken.ts` calls gateway's
`GET /modules/{id}/proxy-token` once per page view to mint a short-lived (5 min), module-scoped JWT;
new `src/modules/useModuleProxyToken.ts` wraps that in the same derived-at-render idle/loading/success/
error hook pattern `useModules.ts` already established, keyed on `moduleId:workspace:accessToken` (mint
once per key change, no polling — a `refetch()` on the error state's Retry button mints a fresh one on
demand instead). `ModuleDetail.tsx`'s new `ModuleFrame` sub-component then renders
`<iframe src="{gateway}/modules/{id}/proxy/?token=...">` — the module's actual content streams back
through gateway's new reverse-proxy route, with the token itself doing double duty as the thing that
tells gateway which `X-Workspace`/`X-User`/`X-Role` to forward, since there's no request header to
derive them from at that point. Full "why a token in a query param, not a cookie or a header" reasoning
lives in `app/module_proxy.py`'s own module docstring (`src/core/gateway`) — this page doesn't
re-derive any of it.

`ModuleDetail.module.css` gained a `.page`/`.frame` split: the existing `.detail` block keeps its
`max-width: 520px` for the metadata column, but the iframe (`.frame`, `width: 100%; height: 70vh`) is a
sibling of it, not nested inside, since a whole other app's UI needs real width. A module installed
before this branch (no `has_own_ui` yet) shows a reworded notice pointing at reinstalling
(`platform module install <id>`) to pick it up, rather than the old "not built yet" language — the
mechanism exists now, that module's `Application` just hasn't been re-rendered with the new
`platform.io/proxy-to` annotation.

**Known limitation** (not fixed by this branch, see gateway's own README for the full explanation): a
module's own follow-up requests (relative `<script src>`/`fetch()` calls its page issues) don't carry
the `?token=` — this is only proven correct end-to-end against `hello-module`, whose content is a
single self-contained page with no follow-up requests of its own.

## What's NOT built yet

That closes out every item on `ui-shell-plan.md`'s build list. Still open, tracked separately in
`docs/known-issues.md` rather than here since it's a platform issue and not specific to any one page:
the `modules-root` prune gap noted above.

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

Vitest arrived `feature/ui-shell-oauth` (item 3) — the trigger the earlier version of this README
named: real pure logic worth exercising (PKCE math, expiry math, JWT-payload decoding), not a static
placeholder page anymore. Scoped narrowly, matching gateway's own "pure logic direct, live for the
rest" discipline: `src/auth/pkce.test.ts` and `tokens.test.ts` cover the PKCE/token-storage math
directly (including RFC 7636 Appendix B's own worked example, not just self-consistency) —
deliberately *not* testing the actual `fetch()` calls, redirects, or component rendering, since
those need a real Keycloak or a rendered DOM to mean anything. Runs in Vitest's default `node`
environment, not `jsdom` — `crypto.subtle` is a Node 22 global but a known `jsdom` gap, and skipping
it also avoids pulling in `@testing-library/react` for component tests.

`feature/ui-shell-nav` (item 5) added `src/workspace/workspaces.test.ts` on the same discipline —
`parseWorkspaceMemberships()`'s parsing/dedupe logic and the `sessionStorage` round-trip are real
pure logic, covered directly with hand-built fixtures, no mocking framework. Deliberately *not*
tested: `useModules`, `WorkspaceContext.tsx`, `Shell.tsx`, `ModuleList.tsx`, `ModuleDetail.tsx`,
`WorkspaceSwitcher.tsx` — all either need a rendered DOM (the same `jsdom` gap above) or are thin
composition with no pure logic of their own once `workspaces.ts` is factored out.

`feature/ui-shell-addons` (item 7) adds `src/addons/` on the same, unchanged discipline: `useAddons.ts`
and `Addons.tsx` are structural copies of already-untested `useModules.ts`/`ModuleList.tsx`, and
`addons/api.ts`'s `fromDto` is a direct field mapping, same as `modules/api.ts`'s own untested
`fromDto` — no new test files.

`feature/ui-shell-addons-mutation` (item 7, Install/Remove) adds one new test file,
`src/addons/mutationState.test.ts` (16 tests) — `mutationState.ts` is exactly the kind of pure,
no-fetch, no-React logic this section's discipline calls for direct coverage, same as
`workspace/workspaces.test.ts`'s precedent: `isInstalled` (the not-installed-sentinel comparison),
`resolveQueuedMutations` (a queued install/uninstall resolving once the catalog flips, staying queued
when it hasn't, ignoring non-queued entries, returning the same `Map` reference when nothing changed —
the same-reference check matters here since it's what lets `useAddonMutations` derive `states` at
render time every render without needless work), `timeOutStaleMutations`, and `hasAnyQueued`.
Deliberately *not* tested, same reasoning as every other hook/component in this section:
`mutations.ts`'s `fetch()` calls, `useAddonMutations.ts` itself, and `Addons.tsx`'s new row/button
state machine — all need a real network call or a rendered DOM to mean anything.

## What can only be confirmed live

Same category as every other containerized-service branch in this repo: the image doesn't exist
until this branch merges to `dev` (`ci.yml`'s `on.push.branches: [dev, test, main]` only builds and
pushes on those three branches, never on a feature branch) — so whether `app.platform.local` is
actually reachable and the page renders can only be checked on `homelab-dev` after merge.

Specific to this branch: run `bootstrap/keycloak-bootstrap-ui-shell-client.sh` once against the real
cluster (new client, one-time step), then a real browser login end to end — "Log in" redirects to
Keycloak, a real login completes, lands back on `/auth/callback` then `/` with `code`/`state`
stripped from the URL, shows the logged-in username; "Log out" clears the session and returns to
logged-out. `post.logout.redirect.uris` needed a real fix, not just confirmation — see
`docs/known-issues.md`'s entry on it: a multi-valued Keycloak client attribute has to be joined with
`##`, not a space, or client creation 400s outright. Fixed in the script; worth re-checking the
actual logout redirect behaves once it's exercised live for the first time.

Specific to `feature/ui-shell-nav` (item 5): after a real login, confirm the URL bar reads `/` with
no leftover `?code&state` (the direct regression check for the `history.replaceState` fix above);
confirm the workspace switcher shows the real group-derived workspace(s) for the logged-in user (or
the "no workspace" empty state, if they have none); confirm switching workspaces fires a fresh
`GET /modules` with the new `X-Workspace` header and the list updates; click a module, confirm the
detail page and the item-8 note; confirm browser back/forward and a hard refresh on
`/modules/<id>` all work (the last one is the real test of `nginx.conf`'s `try_files` SPA fallback
against a route besides `/`, for the first time).

Specific to `feature/ui-shell-addons` (item 7, read-only page): after a real login, confirm the new
"Add-ons" nav link appears and navigates to `/addons`; confirm every module in `src/modules/` appears
with correct display name/icon/`requires`, and its correct status (real Argo CD health if installed,
the "not installed" sentinel otherwise); confirm the page-level "not built yet" notice is visible and
the Install button is disabled with a hover tooltip; if a module is installed, confirm its row links
to the existing `/modules/<id>` detail page with no regression; confirm switching workspaces refetches
the catalog with the new `X-Workspace` header; confirm browser back/forward and a hard refresh on
`/addons` work (same SPA-fallback regression check as `/modules/<id>` above, now for a second route).

Specific to `feature/ui-shell-addons-mutation` (item 7, Install/Remove): with an editor- or owner-role
login, click Install on a not-installed module — confirm the button flips to a disabled "Installing…",
a "queued" note appears, the page polls quietly in the background, and once the triggered workflow
finishes and Argo CD reconciles, the row flips to `Healthy` with real Install/Remove controls and no
manual refresh needed. Click Remove on an installed module — confirm the inline confirm/cancel step
appears with the "can take a few minutes" note, and after confirming, the same queued→polling→resolved
flow; because of the still-open `modules-root` prune gap (`docs/known-issues.md`), this may need the
documented manual `kubectl -n argocd delete application <module-id>` workaround to actually resolve —
worth confirming whether it resolves on its own or needs that nudge, and either way confirm the row
recovers correctly once it does. Confirm the 2-minute timeout path: whether by waiting it out or by
temporarily lowering `POLL_TIMEOUT_MS`, confirm a stuck mutation falls back to the "still processing"
note and a fresh, clickable button rather than a permanently disabled one. Log in as a viewer-role user
and confirm both buttons render disabled with the "Requires at least editor access" tooltip and no
request ever reaches gateway. If a module with an unsatisfied `requires` is reachable in the current
catalog state, click Install on it and confirm the `409`'s `unsatisfied` list renders in the error note
rather than a generic message.
