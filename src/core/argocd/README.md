# Argo CD — the app-of-apps

`root-app.yaml` is always applied by hand (`bootstrap/install.sh`). It points Argo CD at
`apps/core/`, and every `Application` in that folder becomes a reconciled piece of core from then
on. `optional/*-app.yaml` are the same idea, applied conditionally — see "Portability" below.

`apps/core/*.yaml` — one Argo CD `Application` per core infra piece, each pulling from that
project's own upstream chart repo (not Bitnami's general catalog — see the Aug 2025 changes to
Bitnami's free chart/image catalog; Keycloak in particular now goes through the official Operator,
not a chart). Ordered with `argocd.argoproj.io/sync-wave` so things that depend on each other
install in the right order and wait for the previous wave to go healthy first:

| Wave | Apps | Why this order |
|---|---|---|
| 0 | cert-manager, sealed-secrets, reflector, postgres-operator | Foundational, no cross-dependencies |
| 1 | cert-manager-issuers, ingress-nginx, keycloak-operator, postgres-cluster, postgres-backup-plugin | Each needs its wave-0 counterpart's CRDs/controller healthy first — issuers need cert-manager's CRDs, the Postgres Cluster CR needs the operator's CRDs, the Barman Cloud plugin needs cert-manager (its own mTLS certs) and the `cnpg-system` namespace |
| 2 | keycloak-instance, monitoring, postgres-backup, catalog-database | Needs its own operator's CRDs (wave 1) AND postgres-cluster (also wave 1) to exist first — postgres-backup specifically needs the `ObjectStore` CRD that postgres-backup-plugin (wave 1) registers; catalog-database needs `platform-postgres` and its `catalog` role (both wave 1) to exist as something to reference |
| 3 | keycloak-realm | Seeds the workspace-group model (`../auth/realm-platform.yaml`) via a `KeycloakRealmImport` Job against the live admin API — needs the actual `Keycloak` instance (wave 2) running, not just applied |

`apps/optional/<capability>/*.yaml` follow the same wave numbering independently within their own
capability — MetalLB's controller (wave 0) and its IP pool config (wave 1) still need to install in
that order relative to *each other*, same reasoning as above.

`postgres-operator`/`postgres-cluster` (CloudNativePG) are Phase 1 in ARCHITECTURE.md's build order, pulled forward into Phase 0 here because Keycloak needs a database before Phase 1 would otherwise provide one — see the comments in `manifests/postgres-cluster.yaml` and `keycloak-instance.yaml` for the reasoning. WAL-archive backups (`postgres-backup-plugin`, `postgres-backup`) followed once SeaweedFS existed to archive them to — see `manifests/postgres-backup.yaml`. `catalog-database` (Phase 2 kickoff, 2026-09-01) is the same `platform-postgres` cluster hosting a second database via CNPG's declarative `Database` CRD — see `manifests/catalog-database.yaml` and `src/core/catalog-service/README.md`.

`manifests/` — plain Kubernetes resources (not Helm releases) that some of the `apps/` Applications
point at directly: the MetalLB IP pool, the cert-manager ClusterIssuer, the Keycloak CR. These are
the pieces with values specific to *your* network/cluster — each has a `TODO` comment marking what
to fill in before it'll actually go healthy. Nothing here is wrong to leave on defaults temporarily;
Argo CD will just show that Application as degraded until the TODO is addressed.

## Portability: core vs. optional capabilities

Added 2026-08-31, prompted by "would this work on EKS?" The honest answer at the time was "the
GitOps pattern and most individual apps, yes — but MetalLB and Longhorn are bare-metal assumptions
baked into what looked like one fixed 'core'." This is the fix: `apps/` splits into two halves.

**`apps/core/`** — environment-agnostic. Works identically whether the cluster is a homelab box, a
self-hosted data centre, cloud VMs you run k8s on yourself, or a managed service like EKS/GKE/AKS.
Nothing in here assumes anything about what's underneath Kubernetes. Always applied, via
`root-app.yaml`.

**`apps/optional/<capability>/`** — everything that depends on what's underneath. Each capability
has its own small root Application (`optional/<capability>-app.yaml`) applied by `bootstrap/install.sh`
only when that capability's flag says so, exactly the same mechanism as `root-app.yaml` itself (a
plain-directory app-of-apps, `__REPO_URL__`/`__REVISION__` substituted by `sed` before `kubectl apply`).
Three capabilities exist today:

- **`metallb`** — hands out real IPs for `LoadBalancer` Services (like ingress-nginx's) by ARPing on
  the local subnet. Only needed where nothing else does this job. Flag: `--skip-metallb` to opt out
  (default: applied).
- **`storage-longhorn`** — replicated block storage across this cluster's own node disks. Only
  needed where nothing else provides durable/replicated storage. Flag: `--enable-longhorn` to opt in
  (default: not applied — CloudNativePG's `Cluster` deliberately leaves `storage.storageClass` unset,
  so it always uses whatever the cluster's default `StorageClass` is, Longhorn or not).
- **`storage-seaweedfs`** — the in-cluster S3-compatible object store (ARCHITECTURE.md §2's storage
  layer, and what `postgres-backup.yaml`'s WAL-archiving points at). Only needed where nothing else
  already provides S3-compatible storage. Flag: `--skip-seaweedfs` to opt out (default: applied),
  requiring `--s3-endpoint` plus `S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY` env vars pointing at a
  real external endpoint instead (`bootstrap/install.sh --help`). Originally spec'd as MinIO — see
  `apps/optional/storage-seaweedfs/seaweedfs.yaml`'s header for why that changed before anything was
  ever built on it.

Why flags with *independent*, not always matching, defaults instead of one `--profile homelab|cloud`
switch: these are genuinely independent questions ("does something already hand out LoadBalancer
IPs?", "does something already provide durable block storage?", "does something already provide
S3-compatible object storage?"), not one bucket per environment — a cloud-VM cluster you run k8s on
yourself might want cloud block storage but *not* the cloud's LB service (cost), or vice versa.
Flags compose; a fixed enum of named profiles would have forced a guess at every combination anyone
might actually want. The defaults just match what this repo was originally built and tested against
(a self-hosted box with nothing else underneath it providing any of these three things) — so today's
exact zero-flag `bootstrap/install.sh` invocation still does exactly what it always did.

| Environment | `--skip-k3s` | `--skip-metallb` | `--enable-longhorn` | `--skip-seaweedfs` |
|---|---|---|---|---|
| Homelab | no | no | yes, once you have 2+ storage-capable nodes | no |
| Self-hosted data centre | no | no (unless you have real LB hardware) | usually yes | no |
| Cloud VMs, self-managed k8s | no | yes, if using the cloud's LB service | no, if using the cloud's block storage | yes, if using real cloud object storage (S3/R2/B2/etc.) |
| Managed k8s (EKS/GKE/AKS) | yes — point `kubectl`/`KUBECONFIG` at it first | yes | no | yes, point `--s3-endpoint` at real cloud object storage |

What's still a homelab-shaped default even inside `apps/core/`, worth knowing about rather than
hidden: `manifests/cluster-issuer.yaml` ships a self-signed `ClusterIssuer` — that works everywhere,
but isn't what you'd want for anything actually reachable from outside a homelab. Swapping it for a
real ACME/Let's Encrypt issuer is a config change to that one file, not a portability blocker, so it
wasn't pulled into this mechanism — see that file's own comments for the two paths (import
`platform-ca` once, or move to Let's Encrypt when there's a real public domain).

## Self-referencing apps — why `repoURL`/`targetRevision` are hardcoded, not templated

`root-app.yaml` uses `__REPO_URL__`/`__REVISION__` placeholders that `bootstrap/install.sh`
substitutes with `sed` before applying it — that's what lets `install.sh` auto-detect your remote
and current branch instead of hardcoding them. That substitution is a one-time, one-file thing:
it runs only against `root-app.yaml`, right before the script hands it to `kubectl apply`.

Every other `Application` in this folder is read straight from GitHub *by Argo CD itself* once
`root` starts reconciling — there's no script in the loop, and Argo CD has no templating
mechanism for a plain `directory:`-sourced Application. So any Application manifest in here that
points back at this same repo (`postgres-cluster`, `postgres-backup-plugin`, `postgres-backup`,
`catalog-database`, `cert-manager-issuers`, `metallb-config`, `keycloak-instance`, `keycloak-realm`
— the ones sourcing `manifests/*.yaml` or `../auth/*.yaml` from this repo, as opposed to an
external chart) has to use
a real, literal `repoURL`/`targetRevision`, not a placeholder. A placeholder left in one of these
isn't "filled in later" the way it is in `root-app.yaml` — it's a permanently broken source that
Argo CD can never resolve, and it fails silently as a stuck `Unknown` sync status with no obvious
symptom pointing at the cause (this bit us in testing — see `docs/known-issues.md`).

Current convention: these seven hardcode `repoURL: git@github.com:DougallPercival/opendataplatform.git`
and `targetRevision: dev`. If you deliberately bootstrap from a different branch (a feature branch,
say, for isolated testing), `root` itself will track it fine via `--revision`, but these seven will
keep tracking `dev` until you update them by hand — a known, accepted limitation of this pattern
rather than something `install.sh` can fix for you.
