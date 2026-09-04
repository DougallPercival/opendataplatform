# ui-shell + gateway module registry + Add-ons page: a properly-scoped build plan

## Context

`module-lifecycle-plan.md`'s item 7 — "gateway's module registry / `PlatformModule` registrations /
the Add-ons page API, and `ui-shell` itself" — has been deferred twice already: once when that doc
was written (item 6 vs. 7 split), once again when platform-module-deps stayed scoped to item 6 only.
The user picked item 7 up next. Research this pass (docs/ui-shell-plan branch, three parallel
Explore passes over ARCHITECTURE.md, this repo's existing service conventions, and gateway's current
proxy) found the same shape of problem module-lifecycle-plan.md itself was written to solve: item 7
isn't one branch's worth of work, it's several genuinely separate subsystems bundled under one
bullet point, each needing its own real design decision before any of it can be built. This doc
exists so that work is tracked as a real, scoped plan, the same reason `module-lifecycle-plan.md`
itself exists (see that doc's own Context) — it intentionally doesn't re-explain design decisions
ARCHITECTURE.md already made, and focuses on what's genuinely still open.

## What already exists

- `src/core/ui-shell/README.md` — five lines: "the one front door: unified nav... catalog
  browser... pipeline & run status... deep-links into each module's own UI... workspace switcher.
  ARCHITECTURE.md §2. Not built yet." No code, no framework choice, no build tooling anywhere.
- `src/core/gateway/app/argocd.py` (item 6, platform-module-deps branch) — `list_module_applications()`
  lists installed module `Application`s + their Argo CD health via the Kubernetes API. This is the
  live half of what the Add-ons page needs (see item 6 below) — already built, already reusable.
- `src/core/gateway/app/modules.py` — the one existing precedent for "a new gateway router:" auth via
  `require_auth`, error handling via a typed exception → HTTP status, respx-testable design. Any new
  gateway endpoint this doc's items need should follow this same shape.
- `module.yaml`'s `proxyTo` field (`ModuleManifest.proxyTo`, `platform_cli/manifest.py`) — validated
  at load time (required string) but confirmed via repo-wide grep to be read by **nothing else**:
  `render_application_manifest()` never writes it into the deployed `Application`, no test
  references it. 100% inert schema today, not a working mechanism.
- Nothing else. No `PlatformModule` CRD/registration mechanism exists (the string appears exactly
  once in ARCHITECTURE.md, §3, undefined beyond that one mention). No static module index. No
  frontend tooling anywhere in this repo — every Dockerfile today (`gateway`, `catalog-service`) is
  single-stage Python/uvicorn.

## Dependency-ordered build list

Each item names what's actually still undecided, not just unbuilt — several of these were never
specified anywhere, unlike module-lifecycle-plan.md's items, which mostly had an ARCHITECTURE.md
section to point at.

1. **Pick a frontend stack; scaffold `ui-shell` as a real-but-empty app, deployed end-to-end.**
   ARCHITECTURE.md names no framework anywhere (grepped for React/Vue/Svelte/Vite — nothing). The
   one concrete breadcrumb in the whole repo is `catalog-service/.env.example`'s
   `CORS_ORIGINS=http://localhost:5173` — Vite's default dev port — with the comment "for local dev
   against ui-shell... gateway proxies same-origin, no CORS needed there in-cluster." **Recommended:
   React + TypeScript + Vite** — the least-surprising choice given that's the only signal anywhere,
   rather than a fresh framework evaluation with zero constraints. This step also needs: a new
   multi-stage `Dockerfile` (Node build stage → nginx/static-serve runtime stage — the first
   multi-stage build anywhere in this repo, nothing to copy verbatim, only the surrounding
   conventions: non-root user, build-context scoping comment, `EXPOSE`);
   `src/core/argocd/manifests/ui-shell.yaml` (Deployment+Service+Certificate+Ingress, mirroring
   `gateway.yaml`'s structure exactly, host `app.platform.local` — confirmed free, only
   `keycloak.platform.local`/`gateway.platform.local` are claimed anywhere in this repo today); a new
   self-referencing `apps/core/ui-shell.yaml` Application pointer (hardcoded `repoURL`/
   `targetRevision: dev`, same convention every other self-referencing app uses — see
   `argocd/README.md`'s own section on this); and a new `ci.yml` path-filter + test + build-and-push
   job set, mirroring gateway's exactly. No backend calls, no auth, no nav data — just prove
   git push → CI build → Argo deploy → reachable over Ingress with a static placeholder page.

   **✅ Built, 2026-09-04 (feature/ui-shell-scaffold branch)** — see `src/core/ui-shell/README.md`
   for the full writeup. React+TS+Vite scaffold, multi-stage Docker image, `ui-shell.yaml`/
   `apps/core/ui-shell.yaml` at sync wave 4, `ci.yml`'s `test-ui-shell`/`build-and-push-ui-shell`
   pair. Still just a static placeholder page — items 2-8 below remain fully open.
2. **Resolve the same-origin question.** The original design already assumes gateway proxies
   ui-shell in-cluster so the browser never needs CORS (`.env.example`'s own comment) — but gateway's
   proxy today (`app/proxy.py`) forwards to catalog-service only, one fixed `httpx.AsyncClient` built
   once at startup, nothing path-parameterized. Real decision, not a mechanical extension: does
   gateway grow a second, permanent proxy target for ui-shell's static assets (mirroring how
   `catalog_client` is built in `main.py`'s lifespan), or does ui-shell get its own Ingress host and
   gateway grows CORS support instead (something gateway has zero of today — grepped for
   cors/CORS/Access-Control, zero matches)? This blocks item 5 — nav can't call gateway from the
   browser until one of these is chosen.
3. **Browser OAuth2/PKCE login.** `platform-cli`'s device flow (`platform_sdk/keycloak_login.py`) is
   CLI-shaped — poll a device code, no browser redirect involved. ui-shell needs a structurally
   different flow: authorization code + PKCE, a redirect URI, session/token storage in the browser.
   `bootstrap/keycloak-bootstrap-login-client.sh`'s own header already explains why
   `platform-cli`/`platform-cli-login` are deliberately two separate Keycloak clients, never merged
   — a third, browser-shaped client continues that same separation rather than overloading either
   existing one. Nothing in `platform_sdk` is reusable here.
4. **Gateway module registry v1 — installed modules only.** A new endpoint (extending
   `app/modules.py`, reusing `app/argocd.py`'s `list_module_applications()`) that lists installed
   modules WITH their `displayName`/`icon`/`navPath` — which requires propagating those fields from
   `module.yaml` into the deployed `Application` (e.g. as annotations, in
   `render_application_manifest()` — the same place `proxyTo` needs the same treatment for item 8).
   Deliberately installed-modules-only, no static index needed yet — same "the caller already has
   what it needs locally" realization item 6 had for dependency-checking; nav only needs to know
   what's *actually running*, not the full catalog.
5. **ui-shell's real nav**, calling item 4's endpoint once items 2 and 3 are resolved.
6. **Add-ons page — the static release-time module index.** ARCHITECTURE.md §3's own description:
   "`platform-gateway` reads a static module index built from every `modules/*/module.yaml` at
   release time (so it can list modules that aren't installed yet)... overlays it with... live
   registrations... shows each module's state... by reading Argo CD's `Application` status." The
   live-status half is item 6/platform-module-deps' `argocd.py`, already built. The release-time
   static-index half — a new CI/build step scanning `src/modules/*/module.yaml` into a JSON
   artifact something can serve — has no code anywhere. Bigger and separable from item 4's live-only
   registry.
7. **Install/Remove buttons on the Add-ons page.** A real, unanswered trust-boundary question, not
   a UI question: does gateway get git push credentials to actually commit
   `modules-enabled/*.yaml` the way `platform-cli` does from the operator's own local git checkout
   today? Nothing in this repo has ever given a *service* write access to its own source repo.
8. **Reverse-proxying into a module's own UI** ("deep-links into each module's own UI," §2 — the
   only phrase touching this anywhere, undefined beyond that). Needs `proxyTo` actually propagated
   into the deployed `Application` (currently inert, see "What already exists" above) plus a new
   gateway route (e.g. `/modules/{id}/{path:path}`, registered before `proxy_router`'s catch-all,
   building a per-request client the way `proxy.py` builds its request today rather than gateway's
   current single-permanent-client pattern). Proxy vs. iframe vs. plain external link is also
   unresolved — ARCHITECTURE.md never says which.

## Recommended first slice

**Item 1 only.** Scaffold + deploy pipeline, no auth, no data, no dynamic content — proves the
entirely-new infrastructure (frontend build tooling, a multi-stage Docker image, a fourth Ingress
host, a fourth CI job) works end-to-end before any of items 2-8's real design decisions get built on
top of it. Deliberately not committing to a build order beyond "item 1 first," unlike
`module-lifecycle-plan.md`'s own "items 1-5 as one slice" — that slice was one tightly-coupled
mechanism end-to-end (install → reconcile → uninstall), where building item 3 without item 2 already
in place made no sense. Items 2-8 here are more independent: several separate subsystems that happen
to feed the same eventual page, not one pipeline. Each deserves its own scoping pass when it's
picked up, the same way this doc itself is that scoping pass for item 7 as a whole.

## Open questions this doc deliberately doesn't resolve

- **Same-origin proxying vs. CORS** (item 2) — which one gateway actually implements.
- **Git push credentials for gateway** (item 7) — the real trust-boundary question behind
  Install/Remove buttons; today only `platform-cli`, running as the operator locally, ever commits
  to this repo.
- **Proxy vs. iframe vs. external link** for deep-links into a module's own UI (item 8).
- **What `PlatformModule` registrations actually are** — ARCHITECTURE.md's one undefined mention
  (§3). Items 4-5 above sidestep needing an answer (they read Argo CD `Application` state directly,
  the same move item 6 made for dependency-checking) — but if a future need reintroduces this
  concept literally, it still has no schema or mechanism anywhere to build from.
