# catalog-service

`catalog-lite` — the Unity-Catalog analog. Datasets, functions, pipelines, models, workspaces;
every entry carries `workspace_id` + `visibility` (private/workspace/public). Metadata only — code
stays in `platform-sdk`/git. ARCHITECTURE.md §2 (layer 3), §4, §12 ("Catalog: build vs adopt" —
picked lightweight custom over OpenMetadata/DataHub).

Phase 2 kickoff (2026-09-01): the data model and a thin FastAPI service over it now exist. Not a
placeholder — this is real, runnable code — but it's the first piece of Phase 2, not the whole
thing. See "Not yet built" below for what's deliberately left for the next steps.

## What's here

- `app/models.py` — the schema: `workspaces`, `datasets`, `pipelines`, `models`, `functions` +
  `function_versions` (full publish history), `lineage_edges` (polymorphic — a function's lineage
  can point at a dataset, another function, or a pipeline). Every entity table carries
  `workspace_id` + `visibility`, per ARCHITECTURE.md §4 — see that file's module docstring for why
  it's written out per-table rather than through a mixin.
- `app/visibility.py` — the ONE place the read/write rule lives (`private`/`workspace`/`public`,
  and the "public means readable, not writable" asymmetry from §4). Every router goes through this.
- `app/deps.py` — **placeholder auth**: reads `X-Workspace`/`X-User` headers, no JWT verification.
  Real auth is `platform-gateway`'s job once it proxies here (see its own README) — read that
  module's docstring before relying on this past a cluster boundary.
- `app/routers/` — CRUD for each entity type, plus `functions.py`'s `/publish` and `/promote`
  (the `platform-cli function promote --public` §4 calls out by name) and `lineage.py`.
- `migrations/` — Alembic, hand-written initial migration (`0001_initial_schema.py`, no live DB in
  the environment that built this to autogenerate against) — seeds the `personal` workspace to
  match `../auth/realm-platform.yaml`'s seeded Keycloak group.
- `tests/` — `test_visibility.py` (pure logic, no DB) and `test_datasets_api.py` (integration,
  needs a real Postgres — see that file's docstring for a one-line `docker run` to get one).
- `Dockerfile` — builds and runs today (`docker build . && docker run -p 8000:8000 ...` against any
  reachable Postgres with migrations applied), not yet referenced by anything in `argocd/`.

## Running locally

```bash
cp .env.example .env   # edit DATABASE_URL if not using the default local Postgres
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
# http://localhost:8000/docs — FastAPI's generated Swagger UI
```

## Database provisioning (in-cluster)

`../argocd/manifests/postgres-cluster.yaml`'s `managed.roles` now includes a `catalog` role, and
`../argocd/manifests/catalog-database.yaml` declares a `catalog` database on `platform-postgres` via
CNPG's `Database` CRD (`postgresql.cnpg.io/v1`) — the same declarative pattern
`postgres-backup.yaml`'s `ObjectStore` uses, not a manual `CREATE DATABASE` Job, because the CNPG
operator (1.30, well past the CRD's 1.24 introduction) already offers it. `bootstrap/install.sh`
generates `platform-postgres-catalog-credentials` the same way it generates the Keycloak one, and
Reflector mirrors it into `catalog-service` for whenever a Deployment reads it.

## Not yet built

- **Deployment/Service/Ingress + an Argo CD Application.** Blocked on a real decision, not
  forgotten: where does the built image live? (GHCR under this repo's GitHub org is the obvious
  default — matches `git@github.com:DougallPercival/opendataplatform.git` already being GitHub —
  but that's a decision worth making deliberately, along with whether `.github/workflows/ci.yml`
  builds+pushes it on merge, before wiring a Deployment that pulls from somewhere.) Until then this
  runs locally (`uvicorn app.main:app --reload`) or via the Dockerfile directly.
- **A migration Job.** Once there's a Deployment, it needs something to run `alembic upgrade head`
  before the app starts — same shape as `postgres-backup.yaml`'s bucket-creation Job, using this
  same image with a different command (see the Dockerfile's own comment).
- **Real auth.** See `app/deps.py`'s docstring.
- **Membership/roles on workspaces** (owner/editor/viewer, §4's table) — `platform workspace
  invite`, tied to Keycloak group membership, not built yet; today `POST /workspaces` just creates
  a row.
- **platform-sdk / platform-cli** — the actual clients of this API (`@platform.dataset` etc.,
  `platform-cli publish`). Nothing calls this service yet except its own tests.
