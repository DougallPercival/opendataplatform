# Argo CD — the app-of-apps

`root-app.yaml` is the only manifest applied by hand (`bootstrap/install.sh`). It points Argo CD
at `apps/`, and every `Application` in that folder becomes a reconciled piece of core from then on.

`apps/*.yaml` — one Argo CD `Application` per core infra piece, each pulling from that project's
own upstream chart repo (not Bitnami's general catalog — see the Aug 2025 changes to Bitnami's free
chart/image catalog; Keycloak in particular now goes through the official Operator, not a chart).
Ordered with `argocd.argoproj.io/sync-wave` so things that depend on each other install in the
right order and wait for the previous wave to go healthy first:

| Wave | Apps | Why this order |
|---|---|---|
| 0 | cert-manager, sealed-secrets, metallb, storage, postgres-operator | Foundational, no cross-dependencies |
| 1 | cert-manager-issuers, metallb-config, ingress-nginx, keycloak-operator, postgres-cluster | Each needs its wave-0 counterpart's CRDs/controller healthy first — issuers need cert-manager's CRDs, the IP pool needs MetalLB's controller, ingress needs MetalLB for a LoadBalancer IP, the Postgres Cluster CR needs the operator's CRDs |
| 2 | keycloak-instance, monitoring | Needs its own operator's CRDs (wave 1) AND postgres-cluster (also wave 1) to exist first |

`postgres-operator`/`postgres-cluster` (CloudNativePG) are Phase 1 in ARCHITECTURE.md's build order, pulled forward into Phase 0 here because Keycloak needs a database before Phase 1 would otherwise provide one — see the comments in `manifests/postgres-cluster.yaml` and `keycloak-instance.yaml` for the reasoning and what's still deferred (WAL-archive backups to MinIO, once MinIO exists).

`manifests/` — plain Kubernetes resources (not Helm releases) that some of the `apps/` Applications
point at directly: the MetalLB IP pool, the cert-manager ClusterIssuer, the Keycloak CR. These are
the pieces with values specific to *your* network/cluster — each has a `TODO` comment marking what
to fill in before it'll actually go healthy. Nothing here is wrong to leave on defaults temporarily;
Argo CD will just show that Application as degraded until the TODO is addressed.

## Self-referencing apps — why `repoURL`/`targetRevision` are hardcoded, not templated

`root-app.yaml` uses `__REPO_URL__`/`__REVISION__` placeholders that `bootstrap/install.sh`
substitutes with `sed` before applying it — that's what lets `install.sh` auto-detect your remote
and current branch instead of hardcoding them. That substitution is a one-time, one-file thing:
it runs only against `root-app.yaml`, right before the script hands it to `kubectl apply`.

Every other `Application` in this folder is read straight from GitHub *by Argo CD itself* once
`root` starts reconciling — there's no script in the loop, and Argo CD has no templating
mechanism for a plain `directory:`-sourced Application. So any Application manifest in here that
points back at this same repo (`postgres-cluster`, `cert-manager-issuers`, `metallb-config`,
`keycloak-instance` — the ones sourcing `manifests/*.yaml` from this repo, as opposed to an
external chart) has to use a real, literal `repoURL`/`targetRevision`, not a placeholder. A
placeholder left in one of these isn't "filled in later" the way it is in `root-app.yaml` — it's
a permanently broken source that Argo CD can never resolve, and it fails silently as a stuck
`Unknown` sync status with no obvious symptom pointing at the cause (this bit us in testing —
see `docs/known-issues.md`).

Current convention: these four hardcode `repoURL: git@github.com:DougallPercival/opendataplatform.git`
and `targetRevision: dev`. If you deliberately bootstrap from a different branch (a feature branch,
say, for isolated testing), `root` itself will track it fine via `--revision`, but these four will
keep tracking `dev` until you update them by hand — a known, accepted limitation of this pattern
rather than something `install.sh` can fix for you.
