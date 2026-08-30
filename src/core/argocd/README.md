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
| 0 | cert-manager, sealed-secrets, metallb, storage | Foundational, no cross-dependencies |
| 1 | cert-manager-issuers, metallb-config, ingress-nginx, keycloak-operator | Each needs its wave-0 counterpart's CRDs/controller healthy first — issuers need cert-manager's CRDs, the IP pool needs MetalLB's controller, ingress needs MetalLB for a LoadBalancer IP |
| 2 | keycloak-instance, monitoring | Keycloak instance needs its operator's CRDs registered first |

`manifests/` — plain Kubernetes resources (not Helm releases) that some of the `apps/` Applications
point at directly: the MetalLB IP pool, the cert-manager ClusterIssuer, the Keycloak CR. These are
the pieces with values specific to *your* network/cluster — each has a `TODO` comment marking what
to fill in before it'll actually go healthy. Nothing here is wrong to leave on defaults temporarily;
Argo CD will just show that Application as degraded until the TODO is addressed.
