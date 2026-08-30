# catalog-service

`catalog-lite` — the Unity-Catalog analog. Datasets, functions, pipelines, models, workspaces;
every entry carries `workspace_id` + `visibility` (private/workspace/public). Metadata only — code
stays in `platform-sdk`/git. ARCHITECTURE.md §2 (layer 3), §4. Not built yet: Phase 2.

Planned shape: Postgres + FastAPI (§12, "Catalog: build vs adopt" — picked lightweight custom over
OpenMetadata/DataHub).
