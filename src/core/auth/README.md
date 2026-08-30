# auth

Keycloak realm + client + workspace-group config — the `personal` workspace gets seeded here at
Phase 0. ARCHITECTURE.md §2, §4. The Keycloak *instance itself* is provisioned via
`src/core/argocd/apps/keycloak-operator.yaml` + `keycloak-instance.yaml`; what belongs in this
folder is the realm export / client definitions layered on top once that instance is healthy —
not written yet.
