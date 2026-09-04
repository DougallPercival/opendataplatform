# ui-shell

ARCHITECTURE.md §2's "one front door: unified nav... catalog browser... pipeline & run status...
deep-links into each module's own UI... workspace switcher." `docs/architecture/ui-shell-plan.md`
scoped that into 8 separately-decidable pieces; this is item 1 only.

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

## What's NOT built yet

Everything else in `ui-shell-plan.md`'s build list — items 2 through 8: the same-origin-vs-CORS
decision for how ui-shell reaches gateway, browser OAuth2/PKCE login (a new Keycloak client, nothing
reusable from `platform_sdk`'s CLI-shaped device flow), gateway's module registry v1, ui-shell's
real nav, the Add-ons page's static release-time module index, Install/Remove buttons (blocked on a
real trust-boundary question — does gateway get git push credentials?), and reverse-proxying into a
module's own UI. Each is its own future branch and its own scoping decision, not a checklist to work
through in order — see that doc for why.

No test suite yet either — see "Running its tests" below for why that's a deliberate choice at this
scope, not an oversight.

## Running it locally

```bash
npm install
npm run dev
```

Vite's dev server listens on `:5173` by default — the same port `catalog-service/.env.example`'s
`CORS_ORIGINS` breadcrumb already referenced before any of this existed.

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
```

No Vitest yet. There's nothing meaningful to unit-test in a static placeholder page under this
branch's "no dynamic content" scope — `npm run build` succeeding (TypeScript compiles cleanly, Vite
bundles without error) is the right stand-in signal here, the same role `ruff check` + `pytest` play
in CI for the Python services but scoped to what actually exists today. Add a real test harness once
item 5 (real nav) introduces something with actual state or logic worth exercising.

## What can only be confirmed live

Same category as every other containerized-service branch in this repo: the image doesn't exist
until this branch merges to `dev` (`ci.yml`'s `on.push.branches: [dev, test, main]` only builds and
pushes on those three branches, never on a feature branch) — so whether `app.platform.local` is
actually reachable, whether the Ingress/Certificate come up healthy, and whether the placeholder
page actually renders in a real browser can only be checked on `homelab-dev` after merge, not from
this branch alone.
