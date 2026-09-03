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

**Status: not implemented.** Nothing in this document has been built as of 2026-09-03.

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

1. **An Argo CD `Application` watching `modules-enabled/`.** The actual reconciliation engine every
   door in §3 ultimately depends on — until this exists, writing a manifest into
   `modules-enabled/` does nothing. Likely the same directory-watching app-of-apps shape
   `src/core/argocd/root-app.yaml` and `apps/optional/*-app.yaml` already use (see
   `src/core/argocd/README.md`'s "Portability" section for that pattern), pointed at
   `modules-enabled/` instead of `apps/core/` or `apps/optional/<capability>/`. Foundational — every
   later item in this list assumes this is live.

2. **`module.yaml` schema + validation**, per §3's own `notebook-jupyterhub` example: `id`,
   `displayName`, `icon`, `navPath`, `proxyTo`, `healthCheck`, `requires`, `optional`. Needs a real
   validation step somewhere in the install path (reject a malformed `module.yaml` before Argo CD
   ever tries to reconcile whatever it points at) — not specified in §3 beyond the field shape
   itself, so this is genuinely new design work, not just "read what §3 already says."

3. **The chart-wrapper layer §7 references** — `module.yaml`'s placement hint (`platform.io/role:
   control|storage|compute`) turned into a real `nodeSelector`/`tolerations` block on whatever the
   module's chart deploys. Needed before any real module's Helm release reconciles onto the right
   node class; §7 describes the *intent* ("a module's `module.yaml` carries an optional placement
   hint that the chart wrapper turns into the actual `nodeSelector`/`tolerations` block") but not the
   wrapper's actual mechanism (a Helm library chart? a post-render step? a small piece of Go/Python
   templating?) — that choice still needs making here.

4. **`platform-cli module install <name>` / `module uninstall <name> [--purge-data]`.** The CLI
   door. Per §3's "Tearing it all down" subsection: `install` writes the module's manifest into
   `modules-enabled/`; `uninstall` removes it and lets Argo CD prune. `--purge-data` additionally
   drops the module's PersistentVolumeClaims *and* "the workspace bucket prefixes/schemas it
   owned" — that second half needs a real, enforced convention for what a module's owned
   data actually is (which SeaweedFS bucket prefixes, which Postgres schemas) *before* `--purge-data`
   can safely automate deleting it; today nothing declares that ownership anywhere machine-readable.
   Worth its own design pass, not assumed here.

5. **`platform-cli module scaffold <name>`.** Generates `modules/<name>/` (chart + `module.yaml`)
   from `_template/`, per §3's "Building a new module" subsection. The most self-contained item on
   this list — doesn't depend on 1-4 being live, only on `_template/` (which already exists).

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

**Items 6-7 explicitly deferred to their own later branch** — the dependency-check endpoint, the
module registry, the Add-ons page, and `ui-shell` are a substantial, separable piece of work in their
own right (arguably the harder half, since `ui-shell` doesn't exist at all), and nothing in items 1-5
requires them to already exist. Splitting here mirrors how `platform-ingress` and
`catalog-service-netpol` each shipped independently useful, narrowly-scoped pieces rather than one
large branch.

## Open questions this doc deliberately doesn't resolve

- The chart-wrapper mechanism (item 3) — needs a real design decision, not just "per §7."
- The machine-readable "what does this module own" convention `--purge-data` needs (item 4) — could
  be a field on `module.yaml` itself (e.g. `ownedBuckets`/`ownedSchemas`), or something the module's
  own chart declares; not decided here.
- `module.yaml` validation's exact failure mode (item 2) — reject at `scaffold`-time, at
  `install`-time, or let Argo CD's own sync just go Degraded on a bad manifest?

These are exactly the kind of decisions this session's branch-plans usually settle as "Key design
decisions" before implementation starts — left open here on purpose, since settling them without
being about to actually build the thing risks guessing wrong and having to revisit anyway.
