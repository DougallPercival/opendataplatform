# gateway

Module registry, nav aggregation, Add-ons page API, reverse proxy into each module's own UI —
ARCHITECTURE.md §2 (layer 3) and §3. Not built yet; substantive work starts Phase 2.

Reads: `src/modules/*/module.yaml` (the static catalog) + live `PlatformModule` registrations +
Argo CD `Application` status (module health). Talks to: `catalog-service`, Argo CD's API (to drive
Install/Remove from the Add-ons page).
