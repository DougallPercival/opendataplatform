# charts

One Helm chart per module + core service, once those services exist to package (ARCHITECTURE.md
§10). Phase 0's infra pieces don't need charts of their own here — they pull upstream charts
directly via the `Application` manifests in `src/core/argocd/apps/`. This folder starts filling in
once `gateway`/`catalog-service`/`ui-shell` have something to deploy (Phase 2) and the first module
gets built (Phase 3+).
