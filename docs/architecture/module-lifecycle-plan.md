# Module lifecycle: a properly-scoped build plan

## Context

ARCHITECTURE.md §11's Phase 2 build-order row lists `platform module uninstall --purge-data`
alongside `platform-cli function promote` and "git remote," as if the three were comparably-sized
leftover items. Researching that row for the `platform-function-promote` branch (2026-09-03) found
otherwise: `function promote` was a well-scoped, purely client-side gap (catalog-service's backend
already existed) — but `module uninstall --purge-data` isn't buildable in isolation at all. As of
that same date, `src/modules/` and `src/modules-enabled/` are still exactly what §3 describes them
as on paper and nothing more: `src/modules/_template/module.yaml` plus two placeholder READMEs. No
Argo CD `Application` watches `modules-enabled/` yet, `platform module install` doesn't exist,
there's no module registry, and `ui-shell` doesn't exist at all (confirmed independently in
`src/core/gateway/README.md`'s own "What's NOT built yet" section). Building *uninstall* before
*install* exists doesn't make sense — the real task hiding behind that one bullet point is standing
up the whole module lifecycle mechanism ARCHITECTURE.md §3 already designs, which is its own
multi-branch, Phase-3-plus-sized piece of work.

This doc exists so that work is tracked as a real, scoped plan — not silently dropped, and not
re-derived from scratch by whoever eventually picks it up. It intentionally does not re-explain
design decisions ARCHITECTURE.md §3 already made (the three-doors model, `modules/` vs.
`modules-enabled/`, the `module.yaml` shape); it names which section covers each piece and focuses
on build order and what's genuinely still open.

**Status (updated 2026-09-03, platform-module-lifecycle branch): the "Recommended first slice"
below — items 1-5 — is now built.** `src/core/argocd/apps/core/modules-root.yaml`,
`src/platform-cli/platform_cli/{manifest,repo,module}.py`, `src/charts/_template/` +
`src/charts/hello-module/`, and `src/modules/hello-module/module.yaml` are all real, not
placeholders — see each item below for exactly where. Items 6-7 remain open, unchanged from the
original plan. The rest of this document is left as originally written (including "What already
exists," now historical) except where a status note marks something as resolved.

## What already exists

- `src/modules/README.md`, `src/modules-enabled/README.md` — description only, matching what §3
  says these directories are for.
- `src/modules/_template/module.yaml` — the skeleton `platform-cli module scaffold` will eventually
  generate from, per §3's "Building a new module" subsection. Nothing consumes it yet.
- Nothing else. No Argo CD `Application` watches `modules-enabled/`. No `platform module` Typer
  subcommand group in `platform-cli` (contrast with `dataset`/`function`/`workspace`, which exist).
  No module registry, no `PlatformModule` CRD/registration mechanism, no Add-ons page, no `ui-shell`.

## Dependency-ordered build list

Each item names the ARCHITECTURE.md section that already specifies its design, where one exists.

1. **An Argo CD `Application` watching `modules-enabled/`.** ✅ **Built** —
   `src/core/argocd/apps/core/modules-root.yaml`, wave 5. Exactly the app-of-apps shape predicted
   here: same mechanism `root-app.yaml` uses one level up, `directory.recurse: false`, pointed at
   `src/modules-enabled/` instead of `apps/core/`. Each file it finds there is itself a complete
   Argo CD `Application` (see item 4's manifest.py note), not a lightweight pointer.

2. **`module.yaml` schema + validation**, per §3's own `notebook-jupyterhub` example: `id`,
   `displayName`, `icon`, `navPath`, `proxyTo`, `healthCheck`, `requires`, `optional`. ✅ **Built** —
   `src/platform-cli/platform_cli/manifest.py`'s `ModuleManifest` (Pydantic, `extra="forbid"`),
   plus one additive field, `namespace` (defaults to `id`) — resolves the "open questions" section's
   validation-failure-mode question below.

3. **The chart-wrapper layer §7 references** — `module.yaml`'s placement hint (`platform.io/role:
   control|storage|compute`) turned into a real `nodeSelector`/`tolerations` block on whatever the
   module's chart deploys. ✅ **Built** — resolved as a Helm-template convention, not a separate
   wrapper tool: every chart scaffolded from `src/charts/_template/` gets `templates/_helpers.tpl`'s
   `platform.nodeSelector`/`platform.tolerations` named templates for free, and
   `platform module install` computes the actual `placement` values from `module.yaml` and writes
   them into the generated Application's `spec.source.helm.values` block
   (`manifest.py`'s `render_application_manifest`). No separate values file, no post-render step.

4. **`platform-cli module install <name>` / `module uninstall <name> [--purge-data]`.** ✅ **Built**
   — `src/platform-cli/platform_cli/module.py`. `install` validates + renders + writes
   `modules-enabled/<id>.yaml` + commits + pushes (via `repo.py`'s git helpers); `uninstall` mirrors
   it for removal. The PVC-ownership question below is resolved for v1: PVCs only (bucket/schema
   ownership stays open, see below), via a label + `Delete=false` annotation convention
   (`src/charts/hello-module/templates/pvc.yaml`), and `--purge-data` prints the `kubectl delete
   pvc` command rather than running it — platform-cli has no cluster credentials for anything
   beyond git, by design.

5. **`platform-cli module scaffold <name>`.** ✅ **Built** — same file, `module.py`'s `scaffold`
   command. Generates both `modules/<name>/module.yaml` and `charts/<name>/` from their respective
   `_template/` directories; deliberately doesn't commit (see `module.py`'s own docstring for why
   that's different from install/uninstall).

6. **Dependency checking (`requires: [...]`) "at the API layer both doors call through"** (§3's own
   phrasing — "the dependency check lives once, at the API layer both doors call through," referring
   to the CLI door and the Add-ons-page door sharing one check). Needs gateway to grow an endpoint
   for this; gateway's own README already flags the general module-registry-driven proxying work as
   "future work once there's a second module to proxy to" — this is part of that same future work,
   not separate from it.

7. **gateway's module registry / `PlatformModule` registrations / the Add-ons page API**, and
   `ui-shell` itself. Both explicitly out of scope today: `src/core/gateway/README.md`'s "What's NOT
   built yet" section already names the registry/nav-aggregation/Add-ons-page piece as future work,
   and `ui-shell` doesn't exist as a directory at all yet (there's nothing to serve nav *to*). Needed
   for the Add-ons-page door specifically — the CLI door (item 4) and the git door (committing
   straight into `modules-enabled/` by hand) don't depend on either of these.

## Recommended first slice

Not "build the whole system" — a concrete, buildable starting point for whoever picks this up next,
following the same "usable on its own" phasing principle ARCHITECTURE.md §11 already applies between
its numbered phases:

**Items 1-5, against one real, deliberately simple module** (a trivial test module, not
`notebook-jupyterhub` — proving the mechanism shouldn't be coupled to also standing up a real
JupyterHub deployment on the first pass). Concretely: the `modules-enabled/`-watching Application,
`module.yaml` validation, the chart-wrapper's node-placement piece, `platform module
install/uninstall/scaffold`, and a first real end-to-end live verification — install a module via
CLI, confirm Argo CD reconciles it, uninstall it, confirm it's gone, confirm `--purge-data` actually
drops what it claims to.

**Done (2026-09-03):** all of the above is built, against `src/modules/hello-module/` — stock
nginx, `placement: {role: compute}`, one throwaway PVC — per items 1-5's ✅ markers above. Live
verification (install → Argo reconciles → placement lands on the compute node → uninstall leaves
the PVC → `--purge-data` prints the removal command) is tracked separately as this branch's own
live-verification pass, not re-described here — see the branch's own plan file / commit history for
that record rather than duplicating it in this doc.

**Items 6-7 explicitly deferred to their own later branch** — the dependency-check endpoint, the
module registry, the Add-ons page, and `ui-shell` are a substantial, separable piece of work in their
own right (arguably the harder half, since `ui-shell` doesn't exist at all), and nothing in items 1-5
requires them to already exist. Splitting here mirrors how `platform-ingress` and
`catalog-service-netpol` each shipped independently useful, narrowly-scoped pieces rather than one
large branch.

## Open questions this doc deliberately doesn't resolve

Resolved by the platform-module-lifecycle branch (2026-09-03) — kept here, marked, rather than
deleted, so the reasoning stays visible next to the question it answers:

- ~~The chart-wrapper mechanism (item 3)~~ — **resolved**: a Helm-template convention
  (`_helpers.tpl`'s named templates) plus values computed by `platform module install` and passed
  through the generated Application's `spec.source.helm.values`. See item 3 above.
- ~~The machine-readable "what does this module own" convention `--purge-data` needs (item 4)~~ —
  **resolved for v1, PVC-only**: a `platform.io/module: <id>` label plus an
  `argocd.argoproj.io/sync-options: Delete=false` annotation on every PVC a module's chart creates
  (`src/charts/hello-module/templates/pvc.yaml`). Bucket/schema ownership is **still open** —
  deliberately out of scope for this slice, same "PVCs only this pass" scoping the branch that
  built this settled on.
- ~~`module.yaml` validation's exact failure mode (item 2)~~ — **resolved**: rejected at both
  `scaffold`-time (a bad module *name*, not module.yaml content, since scaffold generates the file)
  and `install`-time (a bad module.yaml, including unknown fields — `ModuleManifest`'s
  `extra="forbid"`) — never silently, and never left for Argo CD to discover as a Degraded sync.

Still open, unresolved by this branch — items 6-7's own scope, not this slice's:

- Dependency checking (`requires: [...]`) enforcement — item 6, needs a gateway endpoint.
- The bucket/schema half of `--purge-data`'s data-ownership convention (see above).
- Everything in items 6-7: gateway's module registry, the Add-ons page, `ui-shell`.
